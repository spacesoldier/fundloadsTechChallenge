from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Trace sinks are specified in docs/implementation/kernel/Trace and Context Change Log Spec.md.
import fund_load.adapters.trace_sinks as trace_sinks
from fund_load.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from fund_load.kernel.trace import MessageSignature, TraceRecord


def _record(step_name: str, step_index: int) -> TraceRecord:
    return TraceRecord(
        trace_id="t1",
        scenario="baseline",
        line_no=1,
        step_index=step_index,
        step_name=step_name,
        work_index=0,
        t_enter=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        t_exit=datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC),
        duration_ms=1000.0,
        msg_in=MessageSignature(type_name="A", identity=None, hash=None),
        msg_out=(MessageSignature(type_name="B", identity=None, hash=None),),
        msg_out_count=1,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="ok",
        error=None,
    )


def test_jsonl_trace_sink_emits_one_json_per_line(tmp_path: Path) -> None:
    # Jsonl sink must write one record per line in order (Trace spec §7/10.2).
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="line", flush_every_n=1, fsync_every_n=None)
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["step_name"] == "step-a"
    assert second["step_name"] == "step-b"


def test_jsonl_trace_sink_flush_every_n(tmp_path: Path) -> None:
    # In line mode, flush_every_n controls when flush() is called (Trace spec §7.3).
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="line", flush_every_n=2, fsync_every_n=None)
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["step_name"] == "step-a"
    assert json.loads(lines[1])["step_name"] == "step-b"


def test_stdout_trace_sink_writes_lines(capsys: pytest.CaptureFixture[str]) -> None:
    # Stdout sink is a debug adapter; it writes one JSON line per record (Trace spec §6.2).
    sink = StdoutTraceSink()
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert json.loads(out[0])["step_name"] == "step-a"
    assert json.loads(out[1])["step_name"] == "step-b"


def test_jsonl_trace_sink_batch_mode_buffers_until_threshold(tmp_path: Path) -> None:
    # Batch mode buffers until flush_every_n is reached (Trace spec §7.3).
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="batch", flush_every_n=2, fsync_every_n=None)
    sink.emit(_record("step-a", 0))
    assert path.read_text(encoding="utf-8") == ""
    sink.emit(_record("step-b", 1))
    sink.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_jsonl_trace_sink_flush_writes_buffered_lines(tmp_path: Path) -> None:
    # Explicit flush must write buffered lines in batch mode (Trace spec §7.3).
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="batch", flush_every_n=10, fsync_every_n=None)
    sink.emit(_record("step-a", 0))
    sink.flush()
    sink.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["step_name"] == "step-a"


def test_jsonl_trace_sink_fsync_every_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # fsync_every_n triggers os.fsync calls at the configured cadence (Trace spec §7.3).
    calls: list[int] = []

    def _fake_fsync(fd: int) -> None:
        calls.append(fd)

    monkeypatch.setattr("fund_load.adapters.trace_sinks.os.fsync", _fake_fsync)
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="line", flush_every_n=1, fsync_every_n=1)
    sink.emit(_record("step-a", 0))
    sink.close()
    assert len(calls) == 1


def test_jsonl_trace_sink_serializes_decimal_and_date(tmp_path: Path) -> None:
    # JSONL sink must serialize Decimal/date/datetime via default handler (Trace spec §7.2).
    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="line", flush_every_n=1, fsync_every_n=None)
    record = replace(
        _record("step-a", 0),
        ctx_before={
            "amount": Decimal("1.23"),
            "day": date(2025, 1, 1),
            "ts": datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        },
    )
    sink.emit(record)
    sink.close()
    obj = json.loads(path.read_text(encoding="utf-8").strip())
    assert obj["ctx_before"]["amount"] == "1.23"
    assert obj["ctx_before"]["day"] == "2025-01-01"
    assert obj["ctx_before"]["ts"] == "2025-01-01T00:00:00+00:00"


def test_jsonl_trace_sink_serializes_fallback_objects(tmp_path: Path) -> None:
    # Fallback serialization uses __str__ for unknown objects (Trace spec §7.2).
    class _Thing:
        def __str__(self) -> str:
            return "THING"

    path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=path, write_mode="line", flush_every_n=1, fsync_every_n=None)
    record = replace(_record("step-a", 0), ctx_before={"obj": _Thing()})
    sink.emit(record)
    sink.close()
    obj = json.loads(path.read_text(encoding="utf-8").strip())
    assert obj["ctx_before"]["obj"] == "THING"


def test_stdout_trace_sink_flush_and_close(capsys: pytest.CaptureFixture[str]) -> None:
    # flush/close are no-ops over stdout but must be safe to call (Trace spec §6.1).
    sink = StdoutTraceSink()
    sink.emit(_record("step-a", 0))
    sink.flush()
    sink.close()
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1


def test_trace_sink_as_dict_handles_none_and_passthrough() -> None:
    # Helper supports passthrough for non-dataclass values (Trace spec §7.2).
    assert trace_sinks._as_dict(None) is None
    assert trace_sinks._as_dict({"k": "v"}) == {"k": "v"}
