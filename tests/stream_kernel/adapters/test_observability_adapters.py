from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.observability.adapters import (
    log_jsonl,
    log_stdout,
    telemetry_stdout,
    trace_opentracing_bridge,
    trace_otel_otlp,
    trace_jsonl,
    trace_stdout,
)
from stream_kernel.observability.domain.logging import LogMessage
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


def test_log_and_telemetry_adapters_declare_standard_stream_contract() -> None:
    # Logging and telemetry are stream-mode observability channels in the platform model.
    log_jsonl_meta = get_adapter_meta(log_jsonl)
    log_meta = get_adapter_meta(log_stdout)
    telemetry_meta = get_adapter_meta(telemetry_stdout)
    assert log_jsonl_meta is not None
    assert log_meta is not None
    assert telemetry_meta is not None
    assert list(log_jsonl_meta.consumes) == [LogMessage]
    assert list(log_meta.consumes) == [LogMessage]
    assert list(telemetry_meta.consumes) == [TelemetryMessage]
    assert list(log_jsonl_meta.binds) == [("stream", LogMessage)]
    assert list(log_meta.binds) == [("stream", LogMessage)]
    assert list(telemetry_meta.binds) == [("stream", TelemetryMessage)]


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
    discovered = discover_adapters([module])
    assert set(discovered) == {
        "trace_stdout",
        "trace_jsonl",
        "trace_otel_otlp",
        "trace_opentracing_bridge",
        "log_jsonl",
        "log_stdout",
        "telemetry_stdout",
    }
    # Smoke build for jsonl adapter to ensure factory signature remains valid.
    sink = discovered["trace_jsonl"]({"path": str(Path("trace.jsonl"))})
    assert sink is not None
