from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

import stream_kernel.observability.adapters.tracing as tracing_module
from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.trace_sinks import NoOpTraceSink, OTelOtlpTraceSink
from stream_kernel.observability.adapters import (
    log_file_plain,
    log_jsonl,
    log_stdout_plain,
    log_stdout,
    monitoring_jsonl,
    monitoring_prometheus,
    monitoring_stdout,
    telemetry_stdout,
    trace_opentracing_bridge,
    trace_otel_otlp,
    trace_jsonl,
    trace_stdout,
)
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
    log_plain_file_meta = get_adapter_meta(log_file_plain)
    log_plain_meta = get_adapter_meta(log_stdout_plain)
    log_meta = get_adapter_meta(log_stdout)
    telemetry_meta = get_adapter_meta(telemetry_stdout)
    monitoring_meta = get_adapter_meta(monitoring_stdout)
    monitoring_jsonl_meta = get_adapter_meta(monitoring_jsonl)
    monitoring_prometheus_meta = get_adapter_meta(monitoring_prometheus)
    assert log_jsonl_meta is not None
    assert log_plain_file_meta is not None
    assert log_plain_meta is not None
    assert log_meta is not None
    assert telemetry_meta is not None
    assert monitoring_meta is not None
    assert monitoring_jsonl_meta is not None
    assert monitoring_prometheus_meta is not None
    assert list(log_jsonl_meta.consumes) == [LogMessage]
    assert list(log_plain_file_meta.consumes) == [LogMessage]
    assert list(log_plain_meta.consumes) == [LogMessage]
    assert list(log_meta.consumes) == [LogMessage]
    assert list(telemetry_meta.consumes) == [TelemetryMessage]
    assert list(log_jsonl_meta.binds) == [("stream", LogMessage)]
    assert list(log_plain_file_meta.binds) == [("stream", LogMessage)]
    assert list(log_plain_meta.binds) == [("stream", LogMessage)]
    assert list(log_meta.binds) == [("stream", LogMessage)]
    assert list(telemetry_meta.binds) == [("stream", TelemetryMessage)]
    assert list(monitoring_meta.consumes) == [MonitoringMessage]
    assert list(monitoring_meta.binds) == [("stream", MonitoringMessage)]
    assert list(monitoring_jsonl_meta.consumes) == [MonitoringMessage]
    assert list(monitoring_jsonl_meta.binds) == [("stream", MonitoringMessage)]
    assert list(monitoring_prometheus_meta.consumes) == [MonitoringMessage]
    assert list(monitoring_prometheus_meta.binds) == [("stream", MonitoringMessage)]
    assert log_jsonl_meta.execution_mode == "async"
    assert log_plain_file_meta.execution_mode == "async"
    assert log_plain_meta.execution_mode == "async"
    assert log_meta.execution_mode == "async"
    assert telemetry_meta.execution_mode == "async"
    assert monitoring_meta.execution_mode == "async"
    assert monitoring_jsonl_meta.execution_mode == "async"
    assert monitoring_prometheus_meta.execution_mode == "async"


def test_trace_jsonl_requires_path_setting() -> None:
    with pytest.raises(ValueError):
        trace_jsonl({})


def test_trace_jsonl_accepts_trace_slice() -> None:
    sink = trace_jsonl({"path": "trace.jsonl", "trace_slice": "business_logic"})
    assert sink._trace_slice == "business_logic"  # noqa: SLF001 - contract check for adapter wiring.


def test_trace_jsonl_accepts_grouped_view_alias() -> None:
    sink = trace_jsonl({"path": "trace.jsonl", "view": {"trace_view": "topology"}})
    assert sink._trace_slice == "platform_internals"  # noqa: SLF001 - alias normalization contract.


def test_log_jsonl_can_use_generated_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sink = log_jsonl({})
    assert sink._path.name.startswith("lifecycle_")  # noqa: SLF001 - adapter contract probe.
    assert sink._path.suffix == ".jsonl"  # noqa: SLF001 - adapter contract probe.
    sink.close()


def test_log_file_plain_can_use_generated_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    sink = log_file_plain({})
    assert sink._path.name.startswith("lifecycle_")  # noqa: SLF001 - adapter contract probe.
    assert sink._path.suffix == ".log"  # noqa: SLF001 - adapter contract probe.
    sink.close()


def test_observability_adapters_are_discoverable() -> None:
    # All framework observability adapter factories are discoverable by @adapter metadata.
    module = ModuleType("stream_kernel.observability.adapters")
    module.trace_stdout = trace_stdout
    module.trace_jsonl = trace_jsonl
    module.trace_otel_otlp = trace_otel_otlp
    module.trace_opentracing_bridge = trace_opentracing_bridge
    module.log_jsonl = log_jsonl
    module.log_file_plain = log_file_plain
    module.log_stdout_plain = log_stdout_plain
    module.log_stdout = log_stdout
    module.telemetry_stdout = telemetry_stdout
    module.monitoring_stdout = monitoring_stdout
    module.monitoring_jsonl = monitoring_jsonl
    module.monitoring_prometheus = monitoring_prometheus
    discovered = discover_adapters([module])
    assert set(discovered) == {
        "trace_stdout",
        "trace_jsonl",
        "trace_otel_otlp",
        "trace_opentracing_bridge",
        "log_jsonl",
        "log_file_plain",
        "log_stdout_plain",
        "log_stdout",
        "telemetry_stdout",
        "monitoring_stdout",
        "monitoring_jsonl",
        "monitoring_prometheus",
    }
    # Smoke build for jsonl adapter to ensure factory signature remains valid.
    sink = discovered["trace_jsonl"]({"path": str(Path("trace.jsonl"))})
    assert sink is not None


def test_log_stdout_plain_emits_human_readable_line(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        _ = kwargs
        printed.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", _capture_print)
    sink = log_stdout_plain({})
    sink.emit(
        LogMessage(
            level="debug",
            message="bootstrap.worker_spawned",
            fields={"process_name": "supervisor", "pid": 123},
        )
    )
    assert printed
    line = printed[-1]
    assert "[supervisor-123]" in line
    assert "[DEBUG]" in line
    assert "bootstrap.worker_spawned" in line


def test_log_stdout_plain_prefers_worker_pid_for_worker_events(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        _ = kwargs
        printed.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", _capture_print)
    sink = log_stdout_plain({})
    sink.emit(
        LogMessage(
            level="info",
            message="bootstrap.worker_stop_command",
            fields={
                "process_name": "execution.ingress#1",
                "worker_id": "execution.ingress#1",
                "pid": 100,  # supervisor pid
                "worker_pid": 200,  # worker pid must be rendered in label
            },
        )
    )
    assert printed
    line = printed[-1]
    assert "[execution.ingress#1-200]" in line
    assert "worker_pid=" not in line


def test_log_file_plain_writes_human_readable_line(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.log"
    sink = log_file_plain({"path": str(path)})
    sink.emit(
        LogMessage(
            level="info",
            message="bootstrap.worker_spawned",
            fields={"process_name": "supervisor", "pid": 123, "group_name": "execution.ingress"},
        )
    )
    sink.close()
    data = path.read_text(encoding="utf-8").splitlines()
    assert data
    assert data[-1].startswith("[supervisor-123]: [INFO]: bootstrap.worker_spawned")


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


def test_trace_otel_otlp_passes_view_and_service_name_settings_to_sink() -> None:
    sink = trace_otel_otlp(
        {
            "backend": "urllib",
            "endpoint": "http://collector:4318/v1/traces",
            "service_name_by_step": True,
            "service_name_by_process_group": False,
            "service_name_suffix": ".logical",
            "trace_view": "logical",
            "logical_include_platform_spans": False,
            "topology_include_business_spans": True,
            "isolate_view_ids": True,
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._service_name_by_step is True
    assert sink._service_name_by_process_group is False
    assert sink._service_name_suffix == ".logical"
    assert sink._trace_view == "logical"
    assert sink._logical_include_platform_spans is False
    assert sink._topology_include_business_spans is True
    assert sink._isolate_view_ids is True


def test_trace_otel_otlp_accepts_grouped_settings_and_resolves_backend() -> None:
    sink = trace_otel_otlp(
        {
            "otlp": {"endpoint": "http://collector:4318/v1/traces"},
            "transport": {
                "backend": "urllib",
                "timeout_seconds": 0.5,
                "batch": {"max_items": 32, "flush_interval_ms": 200},
            },
            "service": {
                "service_name": "fund-load",
                "service_namespace": "stream-kernel",
            },
            "view": {
                "trace_view": "topology",
                "service_name_by_process_group": True,
                "topology_include_business_spans": False,
            },
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._backend == "urllib"
    assert sink._endpoint == "http://collector:4318/v1/traces"
    assert sink._timeout_seconds == 0.5
    assert sink._batch_max_items == 32
    assert sink._batch_flush_interval_ms == 200
    assert sink._service_name == "fund-load"
    assert sink._service_namespace == "stream-kernel"
    assert sink._trace_view == "topology"
    assert sink._topology_include_business_spans is False


def test_trace_otel_otlp_transport_group_backend_overrides_settings_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "stream_kernel.observability.adapters.tracing.check_otel_backend_dependencies",
        lambda _backend: None,
    )
    sink = trace_otel_otlp(
        {
            "backend": "urllib",
            "transport": {"backend": "requests"},
            "endpoint": "http://collector:4318/v1/traces",
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._backend == "requests"


def test_trace_otel_otlp_passes_queue_block_timeout_to_sink() -> None:
    sink = trace_otel_otlp(
        {
            "endpoint": "http://collector:4318/v1/traces",
            "queue": {
                "drop_policy": "block_with_timeout",
                "block_timeout_ms": 250,
            },
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._queue_drop_policy == "block_with_timeout"
    assert sink._queue_block_timeout_ms == 250


def test_trace_otel_otlp_applies_near_realtime_batch_defaults() -> None:
    sink = trace_otel_otlp(
        {
            "backend": "urllib",
            "endpoint": "http://collector:4318/v1/traces",
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._batch_max_items == 64
    assert sink._batch_flush_interval_ms == 200
    assert sink._queue_drop_policy == "block_with_timeout"
    assert sink._queue_block_timeout_ms == 100


def test_trace_otel_otlp_rejects_invalid_queue_block_timeout() -> None:
    with pytest.raises(ValueError, match="queue\\.block_timeout_ms must be an integer > 0"):
        trace_otel_otlp(
            {
                "endpoint": "http://collector:4318/v1/traces",
                "queue": {
                    "drop_policy": "block_with_timeout",
                    "block_timeout_ms": 0,
                },
            }
        )


def test_trace_otel_otlp_accepts_block_forever_without_timeout() -> None:
    sink = trace_otel_otlp(
        {
            "endpoint": "http://collector:4318/v1/traces",
            "queue": {
                "drop_policy": "block_forever",
            },
        }
    )
    assert isinstance(sink, OTelOtlpTraceSink)
    assert sink._queue_drop_policy == "block_forever"
