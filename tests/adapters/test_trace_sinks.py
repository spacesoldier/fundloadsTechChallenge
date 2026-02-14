from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Trace sinks are framework infrastructure adapters (framework tracing runtime docs).
import stream_kernel.adapters.trace_sinks as trace_sinks
from stream_kernel.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from stream_kernel.adapters.trace_sinks import OpenTracingBridgeTraceSink, OTelOtlpTraceSink
from stream_kernel.kernel.trace import MessageSignature, RouteInfo, TraceRecord


def _record(step_name: str, step_index: int) -> TraceRecord:
    return TraceRecord(
        trace_id="t1",
        scenario="baseline",
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
    assert "line_no" not in first


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

    monkeypatch.setattr("stream_kernel.adapters.trace_sinks.os.fsync", _fake_fsync)
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


def test_otel_otlp_trace_sink_exports_span_with_trace_id() -> None:
    # P5PRE-OBS-01: OTLP exporter sink must preserve framework trace_id in exported span payload.
    exported: list[dict[str, object]] = []
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        export_fn=lambda span: exported.append(span),
    )
    sink.emit(
        replace(
            _record("step-a", 0),
            route=RouteInfo(
                process_group="execution.features",
                handoff_from="execution.ingress",
                route_hop=1,
            ),
        )
    )
    sink.close()
    assert len(exported) == 1
    assert exported[0]["trace_id"] == "t1"
    assert exported[0]["name"] == "step-a"
    attrs = exported[0]["attributes"]
    assert attrs["process_group"] == "execution.features"
    assert attrs["handoff_from"] == "execution.ingress"
    assert attrs["route_hop"] == 1


def test_opentracing_bridge_sink_maps_operation_and_tags() -> None:
    # P5PRE-OBS-02: OpenTracing bridge sink should map step identity into operation/tags payload.
    exported: list[dict[str, object]] = []
    sink = OpenTracingBridgeTraceSink(
        bridge_name="legacy-tracer",
        emit_fn=lambda span: exported.append(span),
    )
    sink.emit(_record("step-b", 1))
    sink.close()
    assert len(exported) == 1
    span = exported[0]
    assert span["trace_id"] == "t1"
    assert span["operation_name"] == "step-b"
    assert span["tags"]["step_index"] == 1
    assert span["tags"]["scenario"] == "baseline"


def test_otel_otlp_trace_sink_isolates_exporter_failures() -> None:
    # P5PRE-OBS-03: exporter errors must be isolated and never propagate to execution flow.
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        export_fn=lambda _span: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_posts_http_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    # P5PRE-OTLP-01/02: default OTLP sink path should POST JSON payload and propagate configured headers.
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def _fake_urlopen(request, timeout: float = 0.0):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(trace_sinks.urllib_request, "urlopen", _fake_urlopen)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        headers={"authorization": "Bearer test-token"},
        service_name="fund-load",
        timeout_seconds=1.5,
    )
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()

    assert diagnostics["exported"] == 1
    assert diagnostics["dropped"] == 0
    assert captured["url"] == "http://collector:4318/v1/traces"
    assert captured["timeout"] == 1.5
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-type"] == "application/json"
    assert headers["Authorization"] == "Bearer test-token"
    body = json.loads(captured["body"].decode("utf-8"))  # type: ignore[union-attr]
    resource_spans = body["resourceSpans"]
    assert isinstance(resource_spans, list)
    assert resource_spans
    assert body["resourceSpans"][0]["resource"]["attributes"][0]["key"] == "service.name"
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["name"] == "step-a"
    assert spans[0]["kind"] == "SPAN_KIND_INTERNAL"
    assert spans[0]["attributes"]


def test_otel_otlp_trace_sink_network_failures_increment_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # P5PRE-OTLP-03: network exporter failures must be isolated from execution flow.
    def _boom(_request, timeout: float = 0.0):
        _ = timeout
        raise OSError("network down")

    monkeypatch.setattr(trace_sinks.urllib_request, "urlopen", _boom)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces")
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_adds_parent_span_and_process_group_service_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def _fake_urlopen(request, timeout: float = 0.0):
        _ = timeout
        captured["body"] = request.data
        return _Response()

    monkeypatch.setattr(trace_sinks.urllib_request, "urlopen", _fake_urlopen)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        service_name="fund-load",
    )
    sink.emit(
        replace(
            _record("step-a", 0),
            span_id="1111111111111111",
            parent_span_id="0123456789abcdef",
            route=RouteInfo(process_group="execution.features", handoff_from="execution.ingress", route_hop=2),
        )
    )

    body = json.loads(captured["body"].decode("utf-8"))  # type: ignore[union-attr]
    resource_attrs = body["resourceSpans"][0]["resource"]["attributes"]
    resource_keys = {item["key"] for item in resource_attrs}
    assert "service.name" in resource_keys
    assert "process.pid" in resource_keys
    assert "host.name" in resource_keys

    service_name = next(item["value"]["stringValue"] for item in resource_attrs if item["key"] == "service.name")
    assert service_name == "fund-load.execution.features"

    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["spanId"] == "1111111111111111"
    assert span["parentSpanId"] == "0123456789abcdef"
    assert span["kind"] == "SPAN_KIND_INTERNAL"
