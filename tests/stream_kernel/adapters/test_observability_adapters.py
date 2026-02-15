from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

import stream_kernel.observability.adapters.tracing as tracing_module
from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.trace_sinks import NoOpTraceSink
from stream_kernel.observability.adapters import (
    log_jsonl,
    log_stdout,
    telemetry_stdout,
    trace_opentracing_bridge,
    trace_otel_otlp,
    trace_jsonl,
    trace_stdout,
)
from stream_kernel.observability.adapters.monitoring import monitoring_stdout
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.monitoring import MonitoringMessage
from stream_kernel.observability.domain.telemetry import TelemetryMessage
from stream_kernel.observability.domain.tracing import TraceMessage


def test_trace_adapters_declare_standard_kv_stream_contract() -> None:
    # Tracing uses framework-standard kv_stream transport with typed observability model.
    trace_stdout_meta = get_adapter_meta(trace_stdout)
    trace_jsonl_meta = get_adapter_meta(trace_jsonl)
    trace_otel_meta = get_adapter_meta(trace_otel_otlp)
    trace_ot_bridge_meta = get_adapter_meta(trace_opentracing_bridge)
    assert trace_stdout_meta is not None
    assert trace_jsonl_meta is not None
    assert trace_otel_meta is not None
    assert trace_ot_bridge_meta is not None
    assert list(trace_stdout_meta.consumes) == [TraceMessage]
    assert list(trace_jsonl_meta.consumes) == [TraceMessage]
    assert list(trace_otel_meta.consumes) == [TraceMessage]
    assert list(trace_ot_bridge_meta.consumes) == [TraceMessage]
    assert list(trace_stdout_meta.binds) == [("kv_stream", TraceMessage)]
    assert list(trace_jsonl_meta.binds) == [("kv_stream", TraceMessage)]
    assert list(trace_otel_meta.binds) == [("kv_stream", TraceMessage)]
    assert list(trace_ot_bridge_meta.binds) == [("kv_stream", TraceMessage)]
    assert trace_stdout_meta.execution_mode == "async"
    assert trace_jsonl_meta.execution_mode == "async"
    assert trace_otel_meta.execution_mode == "async"
    assert trace_ot_bridge_meta.execution_mode == "async"


def test_log_and_telemetry_adapters_declare_standard_stream_contract() -> None:
    # Logging and telemetry are stream-mode observability channels in the platform model.
    log_jsonl_meta = get_adapter_meta(log_jsonl)
    log_meta = get_adapter_meta(log_stdout)
    telemetry_meta = get_adapter_meta(telemetry_stdout)
    monitoring_meta = get_adapter_meta(monitoring_stdout)
    assert log_jsonl_meta is not None
    assert log_meta is not None
    assert telemetry_meta is not None
    assert monitoring_meta is not None
    assert list(log_jsonl_meta.consumes) == [LogMessage]
    assert list(log_meta.consumes) == [LogMessage]
    assert list(telemetry_meta.consumes) == [TelemetryMessage]
    assert list(log_jsonl_meta.binds) == [("stream", LogMessage)]
    assert list(log_meta.binds) == [("stream", LogMessage)]
    assert list(telemetry_meta.binds) == [("stream", TelemetryMessage)]
    assert list(monitoring_meta.consumes) == [MonitoringMessage]
    assert list(monitoring_meta.binds) == [("stream", MonitoringMessage)]
    assert log_jsonl_meta.execution_mode == "async"
    assert log_meta.execution_mode == "async"
    assert telemetry_meta.execution_mode == "async"
    assert monitoring_meta.execution_mode == "async"


def test_trace_jsonl_requires_path_setting() -> None:
    with pytest.raises(ValueError):
        trace_jsonl({})


def test_log_jsonl_requires_path_setting() -> None:
    with pytest.raises(ValueError):
        log_jsonl({})


def test_observability_adapters_are_discoverable() -> None:
    # All framework observability adapter factories are discoverable by @adapter metadata.
    module = ModuleType("stream_kernel.observability.adapters")
    module.trace_stdout = trace_stdout
    module.trace_jsonl = trace_jsonl
    module.trace_otel_otlp = trace_otel_otlp
    module.trace_opentracing_bridge = trace_opentracing_bridge
    module.log_jsonl = log_jsonl
    module.log_stdout = log_stdout
    module.telemetry_stdout = telemetry_stdout
    module.monitoring_stdout = monitoring_stdout
    discovered = discover_adapters([module])
    assert set(discovered) == {
        "trace_stdout",
        "trace_jsonl",
        "trace_otel_otlp",
        "trace_opentracing_bridge",
        "log_jsonl",
        "log_stdout",
        "telemetry_stdout",
        "monitoring_stdout",
    }
    # Smoke build for jsonl adapter to ensure factory signature remains valid.
    sink = discovered["trace_jsonl"]({"path": str(Path("trace.jsonl"))})
    assert sink is not None


def test_trace_otel_otlp_dependency_missing_is_startup_error_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-K-E-01: missing backend dependency must fail deterministically at adapter build-time.
    monkeypatch.setattr(
        tracing_module,
        "check_otel_backend_dependencies",
        lambda _backend: (_ for _ in ()).throw(ModuleNotFoundError("requests")),
    )
    with pytest.raises(ValueError, match="dependency missing for backend 'requests'"):
        trace_otel_otlp(
            {
                "backend": "requests",
                "endpoint": "http://collector:4318/v1/traces",
            }
        )


def test_trace_otel_otlp_dependency_missing_can_degrade_to_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OBS-K-E-02: explicit degrade mode keeps startup deterministic with no-op sink.
    monkeypatch.setattr(
        tracing_module,
        "check_otel_backend_dependencies",
        lambda _backend: (_ for _ in ()).throw(ModuleNotFoundError("requests")),
    )
    sink = trace_otel_otlp(
        {
            "backend": "requests",
            "endpoint": "http://collector:4318/v1/traces",
            "dependency_missing": "degrade_noop",
        }
    )
    assert isinstance(sink, NoOpTraceSink)
    diagnostics = sink.diagnostics()
    assert diagnostics["reason"] == "trace_otel_otlp:requests:dependency_missing"
