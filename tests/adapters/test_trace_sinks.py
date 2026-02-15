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


def _count_otlp_spans(payload: dict[str, object]) -> int:
    resource_spans = payload.get("resourceSpans", [])
    if not isinstance(resource_spans, list):
        return 0
    count = 0
    for resource_span in resource_spans:
        if not isinstance(resource_span, dict):
            continue
        scope_spans = resource_span.get("scopeSpans", [])
        if not isinstance(scope_spans, list):
            continue
        for scope_span in scope_spans:
            if not isinstance(scope_span, dict):
                continue
            spans = scope_span.get("spans", [])
            if isinstance(spans, list):
                count += len(spans)
    return count


def test_otel_otlp_trace_sink_obs_req_01_posts_via_requests_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-REQ-01: requests backend must export OTLP payload through Session.post.
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return _Response()

        def close(self) -> None:
            return None

    class _Requests:
        @staticmethod
        def Session() -> _Session:
            return _Session()

    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _Requests(),
        raising=False,
    )
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
    )

    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["url"] == "http://collector:4318/v1/traces"
    payload = json.loads(captured["data"].decode("utf-8"))  # type: ignore[union-attr]
    assert _count_otlp_spans(payload) == 1


def test_otel_otlp_trace_sink_obs_req_02_reuses_requests_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-REQ-02: keep-alive session should be reused across multiple spans.
    session_create_count = 0
    call_count = 0

    class _Response:
        status_code = 200

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, data, headers, timeout)
            nonlocal call_count
            call_count += 1
            return _Response()

        def close(self) -> None:
            return None

    class _Requests:
        @staticmethod
        def Session() -> _Session:
            nonlocal session_create_count
            session_create_count += 1
            return _Session()

    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _Requests(),
        raising=False,
    )
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
    )

    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()

    assert session_create_count == 1
    assert call_count == 2


def test_otel_otlp_trace_sink_obs_req_03_respects_headers_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-REQ-03: requests backend must honor configured headers and timeout.
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, data)
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return _Response()

        def close(self) -> None:
            return None

    class _Requests:
        @staticmethod
        def Session() -> _Session:
            return _Session()

    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _Requests(),
        raising=False,
    )
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
        headers={"authorization": "Bearer token"},
        timeout_seconds=3.25,
    )

    sink.emit(_record("step-a", 0))
    sink.close()

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Type"] == "application/json"
    assert headers["authorization"] == "Bearer token"
    assert captured["timeout"] == 3.25


def test_otel_otlp_trace_sink_obs_req_04_transport_exception_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-REQ-04: requests transport errors must not propagate into runner flow.
    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> object:
            _ = (url, data, headers, timeout)
            raise OSError("requests network down")

        def close(self) -> None:
            return None

    class _Requests:
        @staticmethod
        def Session() -> _Session:
            return _Session()

    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _Requests(),
        raising=False,
    )
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
    )

    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_obs_req_05_flushes_batch_by_count_and_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-REQ-05: deterministic flush by item-count and by timer in requests backend.
    posted_payloads_count: list[dict[str, object]] = []
    posted_payloads_timer: list[dict[str, object]] = []

    class _Response:
        status_code = 200

    class _SessionCount:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, headers, timeout)
            posted_payloads_count.append(json.loads(data.decode("utf-8")))
            return _Response()

        def close(self) -> None:
            return None

    class _SessionTimer:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, headers, timeout)
            posted_payloads_timer.append(json.loads(data.decode("utf-8")))
            return _Response()

        def close(self) -> None:
            return None

    class _RequestsCount:
        @staticmethod
        def Session() -> _SessionCount:
            return _SessionCount()

    class _RequestsTimer:
        @staticmethod
        def Session() -> _SessionTimer:
            return _SessionTimer()

    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _RequestsCount(),
        raising=False,
    )
    sink_by_count = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
        batch_max_items=2,
        batch_flush_interval_ms=1000,
        time_fn=lambda: 0.0,
    )
    sink_by_count.emit(_record("step-a", 0))
    sink_by_count.emit(_record("step-b", 1))
    sink_by_count.close()

    assert len(posted_payloads_count) == 1
    assert _count_otlp_spans(posted_payloads_count[0]) == 2

    timer_values = iter([0.0, 0.2])
    monkeypatch.setattr(
        trace_sinks,
        "_import_requests_module",
        lambda: _RequestsTimer(),
        raising=False,
    )
    sink_by_timer = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="requests",
        batch_max_items=10,
        batch_flush_interval_ms=100,
        time_fn=lambda: next(timer_values),
    )
    sink_by_timer.emit(_record("step-c", 2))
    sink_by_timer.emit(_record("step-d", 3))
    sink_by_timer.close()

    assert len(posted_payloads_timer) == 1
    assert _count_otlp_spans(posted_payloads_timer[0]) == 2


def test_otel_otlp_trace_sink_obs_httpx_01_sync_exports_via_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-HTTPX-01: sync httpx mode must export through httpx.Client.
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, http2: bool = False, timeout: float | None = None, limits: object = None) -> None:
            _ = (timeout, limits)
            captured["http2"] = http2

        def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Response:
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return _Response()

        def close(self) -> None:
            captured["closed"] = True

    class _Httpx:
        Client = _Client

    monkeypatch.setattr(trace_sinks, "_import_httpx_module", lambda: _Httpx(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="httpx",
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["url"] == "http://collector:4318/v1/traces"
    payload = json.loads(captured["content"].decode("utf-8"))  # type: ignore[union-attr]
    assert _count_otlp_spans(payload) == 1
    assert captured["closed"] is True


def test_otel_otlp_trace_sink_obs_httpx_02_async_exports_via_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-HTTPX-02: async httpx mode must export through httpx.AsyncClient.
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _AsyncClient:
        def __init__(self, *, http2: bool = False, timeout: float | None = None, limits: object = None) -> None:
            _ = (http2, timeout, limits)
            captured["constructed"] = True

        async def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Response:
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return _Response()

        async def aclose(self) -> None:
            captured["aclose"] = True

    class _Httpx:
        AsyncClient = _AsyncClient

    monkeypatch.setattr(trace_sinks, "_import_httpx_module", lambda: _Httpx(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="httpx",
        httpx_mode="async",
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["constructed"] is True
    assert captured["url"] == "http://collector:4318/v1/traces"
    payload = json.loads(captured["content"].decode("utf-8"))  # type: ignore[union-attr]
    assert _count_otlp_spans(payload) == 1
    assert captured["aclose"] is True


def test_otel_otlp_trace_sink_obs_httpx_03_propagates_http2_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-HTTPX-03: http2 flag must propagate into constructed httpx client.
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, http2: bool = False, timeout: float | None = None, limits: object = None) -> None:
            _ = (timeout, limits)
            captured["http2"] = http2

        def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, content, headers, timeout)
            return _Response()

        def close(self) -> None:
            return None

    class _Httpx:
        Client = _Client

    monkeypatch.setattr(trace_sinks, "_import_httpx_module", lambda: _Httpx(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="httpx",
        httpx_http2=True,
    )
    sink.emit(_record("step-a", 0))
    sink.close()
    assert captured["http2"] is True


def test_otel_otlp_trace_sink_obs_httpx_04_retries_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-HTTPX-04: retry/backoff must be applied deterministically for httpx backend.
    attempts = 0
    sleeps: list[float] = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, http2: bool = False, timeout: float | None = None, limits: object = None) -> None:
            _ = (http2, timeout, limits)

        def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, content, headers, timeout)
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError("transient")
            return _Response()

        def close(self) -> None:
            return None

    class _Httpx:
        Client = _Client

    monkeypatch.setattr(trace_sinks, "_import_httpx_module", lambda: _Httpx(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="httpx",
        retry_max_attempts=2,
        retry_backoff_ms=50,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 1
    assert diagnostics["dropped"] == 0
    assert attempts == 3
    assert sleeps == [0.05, 0.05]


def test_otel_otlp_trace_sink_obs_httpx_05_close_flushes_and_closes_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-HTTPX-05: close() should flush buffered spans and shutdown async client cleanly.
    captured: dict[str, object] = {"posts": 0}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _AsyncClient:
        def __init__(self, *, http2: bool = False, timeout: float | None = None, limits: object = None) -> None:
            _ = (http2, timeout, limits)

        async def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, content, headers, timeout)
            captured["posts"] = int(captured["posts"]) + 1
            return _Response()

        async def aclose(self) -> None:
            captured["aclose"] = True

    class _Httpx:
        AsyncClient = _AsyncClient

    monkeypatch.setattr(trace_sinks, "_import_httpx_module", lambda: _Httpx(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="httpx",
        httpx_mode="async",
        batch_max_items=10,
    )
    sink.emit(_record("step-a", 0))
    sink.close()
    diagnostics = sink.diagnostics()

    assert diagnostics["exported"] == 1
    assert diagnostics["dropped"] == 0
    assert captured["posts"] == 1
    assert captured["aclose"] is True


def test_otel_otlp_trace_sink_obs_aio_01_exports_via_aiohttp_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-AIO-01: aiohttp backend must export payload through ClientSession.
    captured: dict[str, object] = {"posts": 0}

    class _Response:
        status = 200

        async def __aenter__(self) -> "_Response":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            captured["posts"] = int(captured["posts"]) + 1
            return _Response()

        async def close(self) -> None:
            captured["closed"] = True

    class _Aiohttp:
        ClientSession = _Session

    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _Aiohttp(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["url"] == "http://collector:4318/v1/traces"
    payload = json.loads(captured["data"].decode("utf-8"))  # type: ignore[union-attr]
    assert _count_otlp_spans(payload) == 1
    assert captured["posts"] == 1
    assert captured["closed"] is True


def test_otel_otlp_trace_sink_obs_aio_02_queue_drop_policy_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-AIO-02: bounded queue applies drop policy deterministically.
    posted_drop_newest: list[dict[str, object]] = []
    posted_drop_oldest: list[dict[str, object]] = []

    class _Response:
        status = 200

        async def __aenter__(self) -> "_Response":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _SessionDropNewest:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, headers, timeout)
            posted_drop_newest.append(json.loads(data.decode("utf-8")))
            return _Response()

        async def close(self) -> None:
            return None

    class _SessionDropOldest:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, headers, timeout)
            posted_drop_oldest.append(json.loads(data.decode("utf-8")))
            return _Response()

        async def close(self) -> None:
            return None

    class _AiohttpNewest:
        ClientSession = _SessionDropNewest

    class _AiohttpOldest:
        ClientSession = _SessionDropOldest

    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _AiohttpNewest(), raising=False)
    sink_drop_newest = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
        batch_max_items=10,
        queue_max_items=1,
        queue_drop_policy="drop_newest",
    )
    sink_drop_newest.emit(_record("step-a", 0))
    sink_drop_newest.emit(_record("step-b", 1))
    sink_drop_newest.close()
    diag_newest = sink_drop_newest.diagnostics()

    assert diag_newest["exported"] == 1
    assert diag_newest["dropped"] == 1
    assert len(posted_drop_newest) == 1
    spans_newest = posted_drop_newest[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans_newest[0]["name"] == "step-a"

    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _AiohttpOldest(), raising=False)
    sink_drop_oldest = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
        batch_max_items=10,
        queue_max_items=1,
        queue_drop_policy="drop_oldest",
    )
    sink_drop_oldest.emit(_record("step-a", 0))
    sink_drop_oldest.emit(_record("step-b", 1))
    sink_drop_oldest.close()
    diag_oldest = sink_drop_oldest.diagnostics()

    assert diag_oldest["exported"] == 1
    assert diag_oldest["dropped"] == 1
    assert len(posted_drop_oldest) == 1
    spans_oldest = posted_drop_oldest[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans_oldest[0]["name"] == "step-b"


def test_otel_otlp_trace_sink_obs_aio_03_flush_interval_sends_under_low_throughput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-AIO-03: flush interval should trigger batch send when throughput is low.
    posted_payloads: list[dict[str, object]] = []

    class _Response:
        status = 200

        async def __aenter__(self) -> "_Response":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, headers, timeout)
            posted_payloads.append(json.loads(data.decode("utf-8")))
            return _Response()

        async def close(self) -> None:
            return None

    class _Aiohttp:
        ClientSession = _Session

    timer_values = iter([0.0, 0.2])
    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _Aiohttp(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
        batch_max_items=10,
        batch_flush_interval_ms=100,
        time_fn=lambda: next(timer_values),
    )
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()

    assert len(posted_payloads) == 1
    assert _count_otlp_spans(posted_payloads[0]) == 2


def test_otel_otlp_trace_sink_obs_aio_04_shutdown_flushes_pending_within_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-AIO-04: shutdown path flushes pending spans and closes session cleanly.
    captured: dict[str, object] = {"posts": 0}

    class _Response:
        status = 200

        async def __aenter__(self) -> "_Response":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> _Response:
            _ = (url, data, headers, timeout)
            captured["posts"] = int(captured["posts"]) + 1
            return _Response()

        async def close(self) -> None:
            captured["closed"] = True

    class _Aiohttp:
        ClientSession = _Session

    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _Aiohttp(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
        batch_max_items=10,
        aiohttp_shutdown_timeout_seconds=1.0,
    )
    sink.emit(_record("step-a", 0))
    sink.close()
    diagnostics = sink.diagnostics()

    assert diagnostics["exported"] == 1
    assert diagnostics["dropped"] == 0
    assert captured["posts"] == 1
    assert captured["closed"] is True


def test_otel_otlp_trace_sink_obs_aio_05_exporter_exceptions_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-AIO-05: aiohttp exporter failures remain isolated from business execution.
    class _Session:
        def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float) -> object:
            _ = (url, data, headers, timeout)
            raise OSError("aiohttp network down")

        async def close(self) -> None:
            return None

    class _Aiohttp:
        ClientSession = _Session

    monkeypatch.setattr(trace_sinks, "_import_aiohttp_module", lambda: _Aiohttp(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="aiohttp",
    )
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_obs_u3_01_posts_via_poolmanager(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-U3-01: urllib3 backend must export payload using PoolManager.request.
    captured: dict[str, object] = {}

    class _Response:
        status = 200

    class _PoolManager:
        def __init__(self, **kwargs: object) -> None:
            captured["pool_kwargs"] = dict(kwargs)

        def request(
            self,
            method: str,
            url: str,
            *,
            body: bytes,
            headers: dict[str, str],
            timeout: float,
        ) -> _Response:
            captured["method"] = method
            captured["url"] = url
            captured["body"] = body
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return _Response()

        def clear(self) -> None:
            captured["cleared"] = True

    class _Urllib3:
        PoolManager = _PoolManager

    monkeypatch.setattr(trace_sinks, "_import_urllib3_module", lambda: _Urllib3(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="urllib3",
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["method"] == "POST"
    assert captured["url"] == "http://collector:4318/v1/traces"
    payload = json.loads(captured["body"].decode("utf-8"))  # type: ignore[union-attr]
    assert _count_otlp_spans(payload) == 1
    assert captured["cleared"] is True


def test_otel_otlp_trace_sink_obs_u3_02_reuses_poolmanager(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-U3-02: urllib3 PoolManager should be reused across exports.
    pools_created = 0
    requests_sent = 0

    class _Response:
        status = 200

    class _PoolManager:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs
            nonlocal pools_created
            pools_created += 1

        def request(
            self,
            method: str,
            url: str,
            *,
            body: bytes,
            headers: dict[str, str],
            timeout: float,
        ) -> _Response:
            _ = (method, url, body, headers, timeout)
            nonlocal requests_sent
            requests_sent += 1
            return _Response()

        def clear(self) -> None:
            return None

    class _Urllib3:
        PoolManager = _PoolManager

    monkeypatch.setattr(trace_sinks, "_import_urllib3_module", lambda: _Urllib3(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="urllib3")
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()

    assert pools_created == 1
    assert requests_sent == 2


def test_otel_otlp_trace_sink_obs_u3_03_http_error_increments_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-U3-03: status>=400 path should increment dropped and isolate exporter error.
    class _Response:
        status = 503

    class _PoolManager:
        def request(
            self,
            method: str,
            url: str,
            *,
            body: bytes,
            headers: dict[str, str],
            timeout: float,
        ) -> _Response:
            _ = (method, url, body, headers, timeout)
            return _Response()

        def clear(self) -> None:
            return None

    class _Urllib3:
        PoolManager = _PoolManager

    monkeypatch.setattr(trace_sinks, "_import_urllib3_module", lambda: _Urllib3(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="urllib3")
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_obs_u3_04_timeout_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-U3-04: timeout/transport exceptions must not leak into runner path.
    class _PoolManager:
        def request(
            self,
            method: str,
            url: str,
            *,
            body: bytes,
            headers: dict[str, str],
            timeout: float,
        ) -> object:
            _ = (method, url, body, headers, timeout)
            raise TimeoutError("socket timeout")

        def clear(self) -> None:
            return None

    class _Urllib3:
        PoolManager = _PoolManager

    monkeypatch.setattr(trace_sinks, "_import_urllib3_module", lambda: _Urllib3(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="urllib3")
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_obs_u3_05_sets_required_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-U3-05: backend must set application/json content type and forward custom headers.
    captured: dict[str, object] = {}

    class _Response:
        status = 200

    class _PoolManager:
        def request(
            self,
            method: str,
            url: str,
            *,
            body: bytes,
            headers: dict[str, str],
            timeout: float,
        ) -> _Response:
            _ = (method, url, body, timeout)
            captured["headers"] = dict(headers)
            return _Response()

        def clear(self) -> None:
            return None

    class _Urllib3:
        PoolManager = _PoolManager

    monkeypatch.setattr(trace_sinks, "_import_urllib3_module", lambda: _Urllib3(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="http://collector:4318/v1/traces",
        backend="urllib3",
        headers={"authorization": "Bearer token"},
    )
    sink.emit(_record("step-a", 0))
    sink.close()

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Type"] == "application/json"
    assert headers["authorization"] == "Bearer token"


def test_otel_otlp_trace_sink_obs_grpc_01_exports_batch_via_grpc_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-GRPC-01: valid OTLP span batch is sent via gRPC Export call.
    captured: dict[str, object] = {}

    class _Channel:
        def close(self) -> None:
            captured["channel_closed"] = True

    class _Stub:
        def __init__(self, channel: object) -> None:
            captured["stub_channel"] = channel

        def Export(self, request: object, *, timeout: float | None = None) -> object:
            captured["request"] = request
            captured["timeout"] = timeout
            return {"ok": True}

    class _Grpc:
        OTLPTraceServiceStub = _Stub

        @staticmethod
        def insecure_channel(endpoint: str) -> _Channel:
            captured["endpoint"] = endpoint
            return _Channel()

    monkeypatch.setattr(trace_sinks, "_import_grpc_module", lambda: _Grpc(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="collector:4317", backend="grpcio")
    sink.emit(_record("step-a", 0))
    sink.close()

    request = captured["request"]
    assert isinstance(request, dict)
    assert _count_otlp_spans(request) == 1
    assert captured["endpoint"] == "collector:4317"
    assert captured["channel_closed"] is True


def test_otel_otlp_trace_sink_obs_grpc_02_channel_respects_tls_insecure_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-GRPC-02: channel creation respects endpoint and tls/insecure mode.
    captured: dict[str, object] = {}

    class _Channel:
        def close(self) -> None:
            return None

    class _Stub:
        def __init__(self, channel: object) -> None:
            _ = channel

        def Export(self, request: object, *, timeout: float | None = None) -> object:
            _ = (request, timeout)
            return {"ok": True}

    class _Grpc:
        OTLPTraceServiceStub = _Stub

        @staticmethod
        def insecure_channel(endpoint: str) -> _Channel:
            captured["insecure"] = endpoint
            return _Channel()

        @staticmethod
        def secure_channel(endpoint: str, credentials: object) -> _Channel:
            captured["secure"] = (endpoint, credentials)
            return _Channel()

        @staticmethod
        def ssl_channel_credentials() -> object:
            return "creds"

    monkeypatch.setattr(trace_sinks, "_import_grpc_module", lambda: _Grpc(), raising=False)

    sink_insecure = OTelOtlpTraceSink(endpoint="collector:4317", backend="grpcio", grpc_insecure=True)
    sink_insecure.emit(_record("step-a", 0))
    sink_insecure.close()
    assert captured["insecure"] == "collector:4317"

    sink_secure = OTelOtlpTraceSink(endpoint="collector-secure:4317", backend="grpcio", grpc_insecure=False)
    sink_secure.emit(_record("step-b", 1))
    sink_secure.close()
    secure = captured["secure"]
    assert isinstance(secure, tuple)
    assert secure[0] == "collector-secure:4317"
    assert secure[1] == "creds"


def test_otel_otlp_trace_sink_obs_grpc_03_deadline_maps_to_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-GRPC-03: deadline/timeout settings must map to Export timeout option.
    captured: dict[str, object] = {}

    class _Channel:
        def close(self) -> None:
            return None

    class _Stub:
        def __init__(self, channel: object) -> None:
            _ = channel

        def Export(self, request: object, *, timeout: float | None = None) -> object:
            _ = request
            captured["timeout"] = timeout
            return {"ok": True}

    class _Grpc:
        OTLPTraceServiceStub = _Stub

        @staticmethod
        def insecure_channel(endpoint: str) -> _Channel:
            _ = endpoint
            return _Channel()

    monkeypatch.setattr(trace_sinks, "_import_grpc_module", lambda: _Grpc(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="collector:4317",
        backend="grpcio",
        timeout_seconds=2.5,
        grpc_timeout_seconds=1.25,
    )
    sink.emit(_record("step-a", 0))
    sink.close()
    assert captured["timeout"] == 1.25


def test_otel_otlp_trace_sink_obs_grpc_04_retriable_status_uses_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-GRPC-04: retriable gRPC status codes should honor configured retry policy.
    attempts = 0
    sleeps: list[float] = []

    class _Status:
        name = "UNAVAILABLE"

    class _RpcError(Exception):
        def code(self) -> _Status:
            return _Status()

    class _Channel:
        def close(self) -> None:
            return None

    class _Stub:
        def __init__(self, channel: object) -> None:
            _ = channel

        def Export(self, request: object, *, timeout: float | None = None) -> object:
            _ = (request, timeout)
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _RpcError("transient")
            return {"ok": True}

    class _Grpc:
        OTLPTraceServiceStub = _Stub
        RpcError = _RpcError

        @staticmethod
        def insecure_channel(endpoint: str) -> _Channel:
            _ = endpoint
            return _Channel()

    monkeypatch.setattr(trace_sinks, "_import_grpc_module", lambda: _Grpc(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="collector:4317",
        backend="grpcio",
        retry_max_attempts=2,
        retry_backoff_ms=10,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        grpc_retryable_status_codes=("UNAVAILABLE",),
    )
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 1
    assert diagnostics["dropped"] == 0
    assert attempts == 3
    assert sleeps == [0.01, 0.01]


def test_otel_otlp_trace_sink_obs_grpc_05_non_retriable_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-GRPC-05: non-retriable failure increments dropped and does not break execution flow.
    attempts = 0

    class _Status:
        name = "PERMISSION_DENIED"

    class _RpcError(Exception):
        def code(self) -> _Status:
            return _Status()

    class _Channel:
        def close(self) -> None:
            return None

    class _Stub:
        def __init__(self, channel: object) -> None:
            _ = channel

        def Export(self, request: object, *, timeout: float | None = None) -> object:
            _ = (request, timeout)
            nonlocal attempts
            attempts += 1
            raise _RpcError("forbidden")

    class _Grpc:
        OTLPTraceServiceStub = _Stub
        RpcError = _RpcError

        @staticmethod
        def insecure_channel(endpoint: str) -> _Channel:
            _ = endpoint
            return _Channel()

    monkeypatch.setattr(trace_sinks, "_import_grpc_module", lambda: _Grpc(), raising=False)
    sink = OTelOtlpTraceSink(
        endpoint="collector:4317",
        backend="grpcio",
        retry_max_attempts=3,
        grpc_retryable_status_codes=("UNAVAILABLE",),
    )
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1
    assert attempts == 1


def test_otel_otlp_trace_sink_obs_otelsdk_01_initializes_provider_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-OTELSDK-01: provider + processor should be initialized once per sink runtime.
    captured: dict[str, int] = {"provider": 0, "processor": 0, "exporter": 0}

    class _Exporter:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs
            captured["exporter"] += 1

    class _Processor:
        def __init__(self, exporter: object, **kwargs: object) -> None:
            _ = (exporter, kwargs)
            captured["processor"] += 1

    class _Tracer:
        def start_span(self, name: str, *, context: object | None = None) -> object:
            _ = (name, context)

            class _Span:
                def set_attribute(self, key: str, value: object) -> None:
                    _ = (key, value)

                def end(self) -> None:
                    return None

            return _Span()

    class _Provider:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs
            captured["provider"] += 1

        def add_span_processor(self, processor: object) -> None:
            _ = processor

        def get_tracer(self, name: str) -> _Tracer:
            _ = name
            return _Tracer()

        def force_flush(self) -> bool:
            return True

        def shutdown(self) -> bool:
            return True

    class _SDK:
        TracerProvider = _Provider
        BatchSpanProcessor = _Processor
        OTLPHTTPSpanExporter = _Exporter

    monkeypatch.setattr(trace_sinks, "_import_otel_sdk_module", lambda: _SDK(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="otel_sdk")
    sink.emit(_record("step-a", 0))
    sink.emit(_record("step-b", 1))
    sink.close()

    assert captured["provider"] == 1
    assert captured["processor"] == 1
    assert captured["exporter"] == 1


def test_otel_otlp_trace_sink_obs_otelsdk_02_maps_framework_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-OTELSDK-02: framework span/resource attributes should appear in emitted SDK span.
    captured_attrs: dict[str, object] = {}

    class _Exporter:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

    class _Processor:
        def __init__(self, exporter: object, **kwargs: object) -> None:
            _ = (exporter, kwargs)

    class _Span:
        def set_attribute(self, key: str, value: object) -> None:
            captured_attrs[key] = value

        def end(self) -> None:
            return None

    class _Tracer:
        def start_span(self, name: str, *, context: object | None = None) -> _Span:
            _ = (name, context)
            return _Span()

    class _Provider:
        def add_span_processor(self, processor: object) -> None:
            _ = processor

        def get_tracer(self, name: str) -> _Tracer:
            _ = name
            return _Tracer()

        def force_flush(self) -> bool:
            return True

        def shutdown(self) -> bool:
            return True

    class _SDK:
        TracerProvider = _Provider
        BatchSpanProcessor = _Processor
        OTLPHTTPSpanExporter = _Exporter

    monkeypatch.setattr(trace_sinks, "_import_otel_sdk_module", lambda: _SDK(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="otel_sdk")
    sink.emit(
        replace(
            _record("step-a", 0),
            route=RouteInfo(process_group="execution.features", handoff_from="execution.ingress", route_hop=2),
        )
    )
    sink.close()

    assert captured_attrs["scenario"] == "baseline"
    assert captured_attrs["process_group"] == "execution.features"
    assert captured_attrs["stream_kernel.trace_id"] == "t1"


def test_otel_otlp_trace_sink_obs_otelsdk_03_shutdown_flushes_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # OBS-OTELSDK-03: close() should invoke provider force_flush and shutdown.
    captured: dict[str, int] = {"force_flush": 0, "shutdown": 0}

    class _Exporter:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

    class _Processor:
        def __init__(self, exporter: object, **kwargs: object) -> None:
            _ = (exporter, kwargs)

    class _Tracer:
        def start_span(self, name: str, *, context: object | None = None) -> object:
            _ = (name, context)

            class _Span:
                def set_attribute(self, key: str, value: object) -> None:
                    _ = (key, value)

                def end(self) -> None:
                    return None

            return _Span()

    class _Provider:
        def add_span_processor(self, processor: object) -> None:
            _ = processor

        def get_tracer(self, name: str) -> _Tracer:
            _ = name
            return _Tracer()

        def force_flush(self) -> bool:
            captured["force_flush"] += 1
            return True

        def shutdown(self) -> bool:
            captured["shutdown"] += 1
            return True

    class _SDK:
        TracerProvider = _Provider
        BatchSpanProcessor = _Processor
        OTLPHTTPSpanExporter = _Exporter

    monkeypatch.setattr(trace_sinks, "_import_otel_sdk_module", lambda: _SDK(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="otel_sdk")
    sink.emit(_record("step-a", 0))
    sink.close()

    assert captured["force_flush"] == 1
    assert captured["shutdown"] == 1


def test_otel_otlp_trace_sink_obs_otelsdk_04_exporter_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-OTELSDK-04: SDK exporter failures must be isolated and reflected in diagnostics.
    class _Exporter:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

    class _Processor:
        def __init__(self, exporter: object, **kwargs: object) -> None:
            _ = (exporter, kwargs)

    class _Tracer:
        def start_span(self, name: str, *, context: object | None = None) -> object:
            _ = (name, context)
            raise RuntimeError("sdk boom")

    class _Provider:
        def add_span_processor(self, processor: object) -> None:
            _ = processor

        def get_tracer(self, name: str) -> _Tracer:
            _ = name
            return _Tracer()

        def force_flush(self) -> bool:
            return True

        def shutdown(self) -> bool:
            return True

    class _SDK:
        TracerProvider = _Provider
        BatchSpanProcessor = _Processor
        OTLPHTTPSpanExporter = _Exporter

    monkeypatch.setattr(trace_sinks, "_import_otel_sdk_module", lambda: _SDK(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="otel_sdk")
    sink.emit(_record("step-a", 0))
    diagnostics = sink.diagnostics()
    assert diagnostics["exported"] == 0
    assert diagnostics["dropped"] == 1


def test_otel_otlp_trace_sink_obs_otelsdk_05_context_propagation_through_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-OTELSDK-05: trace_id/parent_span_id should be passed into SDK context builder.
    captured_contexts: list[dict[str, object]] = []

    class _Exporter:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

    class _Processor:
        def __init__(self, exporter: object, **kwargs: object) -> None:
            _ = (exporter, kwargs)

    class _Tracer:
        def start_span(self, name: str, *, context: object | None = None) -> object:
            _ = name
            if isinstance(context, dict):
                captured_contexts.append(context)

            class _Span:
                def set_attribute(self, key: str, value: object) -> None:
                    _ = (key, value)

                def end(self) -> None:
                    return None

            return _Span()

    class _Provider:
        def add_span_processor(self, processor: object) -> None:
            _ = processor

        def get_tracer(self, name: str) -> _Tracer:
            _ = name
            return _Tracer()

        def force_flush(self) -> bool:
            return True

        def shutdown(self) -> bool:
            return True

    class _SDK:
        TracerProvider = _Provider
        BatchSpanProcessor = _Processor
        OTLPHTTPSpanExporter = _Exporter

        @staticmethod
        def build_context(*, trace_id: str, span_id: str | None, parent_span_id: str | None) -> dict[str, object]:
            return {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
            }

    monkeypatch.setattr(trace_sinks, "_import_otel_sdk_module", lambda: _SDK(), raising=False)
    sink = OTelOtlpTraceSink(endpoint="http://collector:4318/v1/traces", backend="otel_sdk")
    sink.emit(
        replace(
            _record("step-a", 0),
            span_id="1111111111111111",
            parent_span_id="0123456789abcdef",
        )
    )
    sink.close()

    assert len(captured_contexts) == 1
    context = captured_contexts[0]
    assert context["trace_id"] == "t1"
    assert context["parent_span_id"] == "0123456789abcdef"
