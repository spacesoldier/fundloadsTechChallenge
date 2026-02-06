from __future__ import annotations

import pytest

# Helper coverage targets for runtime wiring (docs/framework/initial_stage/Tracing runtime.md).
from stream_kernel.app.runtime import (
    _build_adapter_instances,
    _build_injection_registry,
    _build_tracing,
    _detect_implicit_sinks,
    _emit_implicit_sink_diagnostics,
    _resolve_symbol,
    _scenario_name,
)
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.kernel.trace import TraceRecorder, TraceRecord
from stream_kernel.application_context.injection_registry import InjectionRegistry


def test_resolve_symbol_rejects_invalid_path() -> None:
    # Symbol resolution rejects malformed paths (Configuration spec §2.1).
    with pytest.raises(ValueError):
        _resolve_symbol("nope")


def test_build_adapter_instances_requires_mapping_config() -> None:
    # Adapter configs must be mappings (Configuration spec §2.1).
    with pytest.raises(ValueError):
        _build_adapter_instances({"input_source": "nope"})


def test_build_adapter_instances_requires_callable_factory() -> None:
    # Factory path must resolve to a callable (Adapter registry spec).
    adapters = {
        "input_source": {
            "factory": "datetime:UTC",
            "settings": {},
        },
    }
    with pytest.raises(ValueError):
        _build_adapter_instances(adapters)


def test_build_adapter_instances_requires_settings_mapping() -> None:
    # Factory settings must be a mapping (Configuration spec §2.1).
    adapters = {
        "input_source": {
            "factory": "builtins:dict",
            "settings": "nope",
        },
    }
    with pytest.raises(ValueError):
        _build_adapter_instances(adapters)


def test_build_adapter_instances_requires_factory_string() -> None:
    # Factory path must be a string (Configuration spec §2.1).
    adapters = {
        "input_source": {
            "factory": 123,
            "settings": {},
        },
    }
    with pytest.raises(ValueError):
        _build_adapter_instances(adapters)


def test_build_injection_registry_requires_bind_list() -> None:
    # Adapter binds must be a list of mappings (Configuration spec §2.1).
    adapters = {"input_source": {"binds": "nope"}}
    instances = {"input_source": object()}
    with pytest.raises(ValueError):
        _build_injection_registry(adapters, instances)


def test_build_injection_registry_requires_bind_mapping() -> None:
    # Each bind entry must be a mapping with port_type/type (Configuration spec §2.1).
    adapters = {"input_source": {"binds": ["nope"]}}
    instances = {"input_source": object()}
    with pytest.raises(ValueError):
        _build_injection_registry(adapters, instances)


def test_build_injection_registry_requires_bind_fields() -> None:
    # Bind entries must define port_type + type (Adapter wiring spec).
    adapters = {"input_source": {"binds": [{"port_type": "stream"}]}}
    instances = {"input_source": object()}
    with pytest.raises(ValueError):
        _build_injection_registry(adapters, instances)


def test_build_injection_registry_returns_registry() -> None:
    # Valid binds should return an InjectionRegistry instance.
    adapters = {
        "input_source": {
            "binds": [{"port_type": "stream", "type": "builtins:dict"}],
        }
    }
    instances = {"input_source": object()}
    registry = _build_injection_registry(adapters, instances)
    assert isinstance(registry, InjectionRegistry)


def test_build_injection_registry_skips_non_mapping_roles() -> None:
    # Non-mapping adapter entries are ignored (defensive behavior).
    adapters = {"input_source": "nope"}
    instances = {}
    registry = _build_injection_registry(adapters, instances)
    assert isinstance(registry, InjectionRegistry)


def test_build_tracing_rejects_invalid_jsonl_mapping() -> None:
    # JSONL sink expects a mapping with string path (Trace runtime spec).
    runtime = {
        "tracing": {"enabled": True, "sink": {"kind": "jsonl", "jsonl": "nope"}},
    }
    with pytest.raises(ValueError):
        _build_tracing(runtime)


def test_build_tracing_rejects_invalid_jsonl_path() -> None:
    # JSONL sink path must be string (Trace runtime spec).
    runtime = {
        "tracing": {"enabled": True, "sink": {"kind": "jsonl", "jsonl": {"path": 123}}},
    }
    with pytest.raises(ValueError):
        _build_tracing(runtime)


def test_build_tracing_supports_stdout_sink() -> None:
    # stdout sink is supported for quick debugging (Trace runtime spec).
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "stdout"}}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is not None


def test_build_tracing_normalizes_signature_and_context_diff() -> None:
    # Non-mapping signature/context_diff should be normalized to defaults (Trace runtime spec).
    runtime = {"tracing": {"enabled": True, "signature": "nope", "context_diff": "nope"}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is None


def test_build_tracing_returns_recorder_without_sink_config() -> None:
    # Missing sink config should return recorder with no sink.
    runtime = {"tracing": {"enabled": True, "sink": "nope"}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is None


def test_build_tracing_unknown_sink_kind_returns_none_sink() -> None:
    # Unknown sink kinds should return recorder without sink (Trace runtime spec).
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "unknown"}}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is None


def test_scenario_name_falls_back_when_missing() -> None:
    # Scenario name fallback should be deterministic (Configuration spec §2.1).
    assert _scenario_name({"scenario": "nope"}) == "scenario"


class _Token:
    pass


class _OtherToken:
    pass


def test_detect_implicit_sink_when_adapter_not_injected() -> None:
    # Adapters that consume but are not injected become implicit sinks (Ports and adapters model §5.2).
    adapters = {"sink": {"consumes": [_Token]}}
    instances = {"sink": object()}
    steps = [StepSpec(name="noop", step=lambda msg, ctx: [])]
    implicit = _detect_implicit_sinks(adapters, instances, steps)
    assert implicit == [("sink", [_Token])]


def test_detect_implicit_sink_ignores_injected_adapter() -> None:
    # If an adapter instance is injected into a step, it is not implicit.
    adapter = object()

    class _Step:
        def __init__(self, sink: object) -> None:
            self.sink = sink

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return []

    adapters = {"sink": {"consumes": [_Token]}}
    instances = {"sink": adapter}
    steps = [StepSpec(name="uses_adapter", step=_Step(adapter))]
    implicit = _detect_implicit_sinks(adapters, instances, steps)
    assert implicit == []


def test_detect_implicit_sink_rejects_invalid_consumes() -> None:
    # consumes must be a list when provided (Ports and adapters model §5.1).
    adapters = {"sink": {"consumes": "nope"}}
    instances = {"sink": object()}
    steps = [StepSpec(name="noop", step=lambda msg, ctx: [])]
    with pytest.raises(ValueError):
        _detect_implicit_sinks(adapters, instances, steps)


def test_emit_implicit_sink_diagnostic_to_trace_sink() -> None:
    # Diagnostics should emit a trace record when a sink is implicit.
    class _Sink:
        def __init__(self) -> None:
            self.records: list[TraceRecord] = []

        def emit(self, record: TraceRecord) -> None:
            self.records.append(record)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    adapters = {"sink": {"consumes": [_OtherToken]}}
    instances = {"sink": object()}
    steps = [StepSpec(name="noop", step=lambda msg, ctx: [])]
    sink = _Sink()
    _emit_implicit_sink_diagnostics(
        {"adapters": adapters},
        steps,
        adapter_instances=instances,
        trace_recorder=TraceRecorder(),
        trace_sink=sink,
        run_id="run",
        scenario_id="scenario",
    )
    assert len(sink.records) == 1
    assert sink.records[0].error is not None
    assert "Implicit sink adapter" in sink.records[0].error.message
