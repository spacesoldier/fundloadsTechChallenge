from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

# TraceRecorder behavior is specified in docs/implementation/kernel/Trace and Context Change Log Spec.md.
from stream_kernel.kernel.context import Context
from stream_kernel.kernel.trace import TraceRecorder


@dataclass(frozen=True, slots=True)
class _Msg:
    # Minimal message with an id to exercise identity/hash extraction.
    id: str
    value: str = "x"


def _context() -> Context:
    return Context(
        trace_id="trace-1",
        run_id="run-1",
        scenario_id="scenario-1",
        line_no=1,
        received_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
    )


def test_trace_record_has_required_fields_and_duration() -> None:
    # Required fields and duration behavior are listed in Trace spec §10.1.
    ctx = _context()
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.step_name == "step-a"
    assert record.step_index == 0
    assert record.scenario == ctx.scenario_id
    assert record.trace_id == ctx.trace_id
    assert record.duration_ms >= 0
    assert ctx.trace[-1] is record


def test_trace_signature_type_only_omits_identity_and_hash() -> None:
    # Signature mode "type_only" must omit identity/hash (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.msg_in.identity is None
    assert record.msg_in.hash is None
    assert record.msg_out[0].identity is None
    assert record.msg_out[0].hash is None


def test_trace_signature_hash_is_stable_for_same_message() -> None:
    # Hash mode must produce stable hash for identical snapshots (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")
    span1 = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record1 = recorder.finish(
        ctx=ctx,
        span=span1,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    span2 = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record2 = recorder.finish(
        ctx=ctx,
        span=span2,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record1.msg_in.hash == record2.msg_in.hash
    assert record1.msg_in.hash is not None


def test_trace_context_diff_respects_whitelist() -> None:
    # Context diff whitelist behavior is required by Trace spec §11.4.
    ctx = _context()
    recorder = TraceRecorder(
        signature_mode="type_only",
        context_diff_mode="whitelist",
        context_diff_whitelist=("tags",),
    )
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    ctx.tags["a"] = "1"
    ctx.metrics["m"] = 1.0
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.ctx_diff is not None
    assert "tags" in record.ctx_diff
    assert "metrics" not in record.ctx_diff


def test_trace_signature_type_and_identity_supports_dict_messages() -> None:
    # type_and_identity should extract id from dict payloads (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="type_and_identity", context_diff_mode="none")
    payload = {"id": "42", "value": "x"}
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=payload,
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[payload],
        status="ok",
        error=None,
    )
    assert record.msg_in.identity == "42"
    assert record.msg_in.hash is None


def test_trace_hash_uses_object_dict_snapshot() -> None:
    # Hash mode should handle plain objects via __dict__ (Trace spec §4.1).
    class Obj:
        def __init__(self, id_value: str) -> None:
            self.id = id_value
            self.value = "x"

    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")
    msg = Obj("7")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=msg,
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[msg],
        status="ok",
        error=None,
    )
    assert record.msg_in.hash is not None
    assert record.msg_in.identity == "7"


def test_trace_snapshot_truncates_long_strings() -> None:
    # Truncation is required for oversized values (Trace spec §11.9).
    ctx = _context()
    ctx.trace_id = "abcdefg"
    recorder = TraceRecorder(
        signature_mode="type_only",
        context_diff_mode="whitelist",
        context_diff_whitelist=("trace_id",),
        max_value_len=5,
    )
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.ctx_before is not None
    # Truncation uses a string suffix marker per Trace spec §11.9.
    truncated_value = record.ctx_before["trace_id"]
    assert isinstance(truncated_value, str)
    assert truncated_value.endswith("...(truncated)")


def test_trace_context_diff_debug_includes_full_snapshot() -> None:
    # Debug mode snapshots full context (Trace spec §4.2).
    ctx = _context()
    ctx.tags["a"] = "1"
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="debug")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    ctx.tags["b"] = "2"
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.ctx_before is not None
    assert record.ctx_after is not None
    assert "tags" in record.ctx_before
    assert "tags" in record.ctx_after


def test_trace_context_diff_handles_none_snapshot() -> None:
    # When snapshots are None, diff should be empty (Trace spec §11.5).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_Msg(id="1"),
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_Msg(id="1")],
        status="ok",
        error=None,
    )
    assert record.ctx_before is None
    assert record.ctx_after is None
    assert record.ctx_diff is None


def test_trace_signature_without_id_returns_none_identity() -> None:
    # Messages without id should not expose identity (Trace spec §4.1).
    class _NoId:
        value = "x"

    ctx = _context()
    recorder = TraceRecorder(signature_mode="type_and_identity", context_diff_mode="none")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=_NoId(),
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[_NoId()],
        status="ok",
        error=None,
    )
    assert record.msg_in.identity is None


def test_trace_hash_uses_dataclass_snapshot() -> None:
    # Dataclass snapshots should hash deterministically (Trace spec §4.1).
    @dataclass(frozen=True, slots=True)
    class _Data:
        id: str
        value: str

    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")
    msg = _Data(id="9", value="x")
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=msg,
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[msg],
        status="ok",
        error=None,
    )
    assert record.msg_in.hash is not None
    assert record.msg_in.identity == "9"


def test_trace_message_snapshot_supports_dict_and_fallback() -> None:
    # Hash mode should accept dict snapshots and fallback to string for non-objects (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")

    span_dict = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in={"id": "1", "value": "x"},
    )
    record_dict = recorder.finish(
        ctx=ctx,
        span=span_dict,
        msg_out=[{"id": "1", "value": "x"}],
        status="ok",
        error=None,
    )
    assert record_dict.msg_in.hash is not None

    span_fallback = recorder.begin(
        ctx=ctx,
        step_name="step-b",
        step_index=1,
        work_index=0,
        msg_in=123,
    )
    record_fallback = recorder.finish(
        ctx=ctx,
        span=span_fallback,
        msg_out=[123],
        status="ok",
        error=None,
    )
    assert record_fallback.msg_in.hash is not None


def test_trace_diff_with_missing_snapshots_is_empty_dict() -> None:
    # Internal diff helper should yield empty dict when before/after is None (Trace spec §11.5).
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    assert recorder._diff_context(None, None) == {}


def test_trace_hash_json_default_handles_datetime_and_decimal() -> None:
    # Hashing should support datetime/Decimal via JSON default (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")
    payload = {"id": "1", "when": ctx.received_at, "amount": Decimal("1.23")}
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=payload,
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[payload],
        status="ok",
        error=None,
    )
    assert record.msg_in.hash is not None


def test_trace_hash_json_default_falls_back_to_string() -> None:
    # Non-serializable objects should fall back to string conversion (Trace spec §4.1).
    ctx = _context()
    recorder = TraceRecorder(signature_mode="hash", context_diff_mode="none")
    payload = {"id": "1", "value": object()}
    span = recorder.begin(
        ctx=ctx,
        step_name="step-a",
        step_index=0,
        work_index=0,
        msg_in=payload,
    )
    record = recorder.finish(
        ctx=ctx,
        span=span,
        msg_out=[payload],
        status="ok",
        error=None,
    )
    assert record.msg_in.hash is not None
