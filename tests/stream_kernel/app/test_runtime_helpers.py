from __future__ import annotations

from types import ModuleType

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import (
    _build_adapter_bindings,
    _build_adapter_instances_from_registry,
    _build_injection_registry_from_bindings,
    _build_tracing,
    _detect_implicit_sinks,
    _emit_implicit_sink_diagnostics,
    _resolve_runtime_adapters,
    _scenario_name,
)
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.kernel.trace import TraceRecord, TraceRecorder


class _Token:
    pass


class _OtherToken:
    pass


class _StreamPort:
    pass


class _KvPort:
    pass


@adapter(name="source", kind="test.source", consumes=[], emits=[_Token], binds=[("stream", _StreamPort)])
def _source_factory(settings: dict[str, object]) -> object:
    return object()


@adapter(name="sink", kind="test.sink", consumes=[_Token], emits=[], binds=[("stream", _StreamPort)])
def _sink_factory(settings: dict[str, object]) -> object:
    return object()


@adapter(
    name="other_sink",
    kind="test.other_sink",
    consumes=[_OtherToken],
    emits=[],
    binds=[("kv", _KvPort)],
)
def _other_sink_factory(settings: dict[str, object]) -> object:
    return object()


def test_resolve_runtime_adapters_requires_mapping_role_config() -> None:
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"sink": "nope"}, discovery_modules=[])  # type: ignore[arg-type]


def test_resolve_runtime_adapters_rejects_kind_in_yaml() -> None:
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"sink": {"kind": "legacy"}}, discovery_modules=[])


def test_resolve_runtime_adapters_rejects_unknown_adapter_name(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("fake.adapters")
    module.source = _source_factory
    monkeypatch.setattr("stream_kernel.app.runtime.importlib.import_module", lambda _name: module)
    with pytest.raises(ValueError):
        _resolve_runtime_adapters(adapters={"missing": {}}, discovery_modules=["fake.adapters"])


def test_build_adapter_bindings_requires_supported_port_type() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    with pytest.raises(ValueError):
        _build_adapter_bindings(
            {"source": {"binds": ["kv"]}},
            registry,
        )


def test_build_adapter_bindings_resolves_typed_ports() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    bindings = _build_adapter_bindings(
        {"source": {"binds": ["stream"]}},
        registry,
    )
    assert bindings["source"] == [("stream", _StreamPort)]


def test_build_adapter_instances_from_registry_requires_mapping() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        _build_adapter_instances_from_registry({"source": "nope"}, registry)  # type: ignore[arg-type]


def test_build_injection_registry_from_bindings_requires_instance() -> None:
    with pytest.raises(ValueError):
        _build_injection_registry_from_bindings({}, {"source": [("stream", _StreamPort)]})


def test_build_tracing_rejects_invalid_jsonl_mapping() -> None:
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "jsonl", "jsonl": "nope"}}}
    with pytest.raises(ValueError):
        _build_tracing(runtime)


def test_build_tracing_rejects_invalid_jsonl_path() -> None:
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "jsonl", "jsonl": {"path": 123}}}}
    with pytest.raises(ValueError):
        _build_tracing(runtime)


def test_build_tracing_supports_stdout_sink() -> None:
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "stdout"}}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is not None


def test_build_tracing_returns_recorder_without_sink_config() -> None:
    runtime = {"tracing": {"enabled": True, "sink": "nope"}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is None


def test_build_tracing_unknown_sink_kind_returns_none_sink() -> None:
    runtime = {"tracing": {"enabled": True, "sink": {"kind": "unknown"}}}
    recorder, sink = _build_tracing(runtime)
    assert recorder is not None
    assert sink is None


def test_scenario_name_falls_back_when_missing() -> None:
    assert _scenario_name({"scenario": "nope"}) == "scenario"


def test_detect_implicit_sink_when_adapter_not_injected() -> None:
    registry = AdapterRegistry()
    registry.register("sink", "sink", _sink_factory)
    adapters = {"sink": {"settings": {}}}
    instances = {"sink": object()}
    steps = [StepSpec(name="noop", step=lambda msg, ctx: [])]
    implicit = _detect_implicit_sinks(adapters, instances, steps, adapter_registry=registry)
    assert implicit == [("sink", [_Token])]


def test_detect_implicit_sink_ignores_injected_adapter() -> None:
    adapter_instance = object()

    class _Step:
        def __init__(self, sink: object) -> None:
            self.sink = sink

        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return []

    registry = AdapterRegistry()
    registry.register("sink", "sink", _sink_factory)
    adapters = {"sink": {"settings": {}}}
    instances = {"sink": adapter_instance}
    steps = [StepSpec(name="uses_adapter", step=_Step(adapter_instance))]
    implicit = _detect_implicit_sinks(adapters, instances, steps, adapter_registry=registry)
    assert implicit == []


def test_emit_implicit_sink_diagnostic_to_trace_sink() -> None:
    class _Sink:
        def __init__(self) -> None:
            self.records: list[TraceRecord] = []

        def emit(self, record: TraceRecord) -> None:
            self.records.append(record)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    registry = AdapterRegistry()
    registry.register("sink", "sink", _other_sink_factory)
    adapters = {"sink": {"settings": {}}}
    instances = {"sink": object()}
    steps = [StepSpec(name="noop", step=lambda msg, ctx: [])]
    sink = _Sink()
    _emit_implicit_sink_diagnostics(
        {"adapters": adapters},
        steps,
        adapter_instances=instances,
        adapter_registry=registry,
        trace_recorder=TraceRecorder(),
        trace_sink=sink,
        run_id="run",
        scenario_id="scenario",
    )
    assert len(sink.records) == 1
    assert sink.records[0].error is not None
    assert "Implicit sink adapter" in sink.records[0].error.message
