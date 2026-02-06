from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import run_with_config
from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry


def test_run_with_config_builds_consumer_registry(monkeypatch) -> None:
    # Runtime should attach ConsumerRegistry built from discovery (Execution runtime §4.2).
    captured: dict[str, object] = {}
    sentinel_registry = InMemoryConsumerRegistry()

    def _build_consumer_registry(self: ApplicationContext) -> InMemoryConsumerRegistry:
        captured["registry_built"] = True
        return sentinel_registry

    def _build_scenario(self: ApplicationContext, *, scenario_id, step_names, wiring):
        # Capture wiring passed from runtime.
        captured["consumer_registry"] = wiring.get("consumer_registry")
        return SimpleNamespace(steps=[])

    def _discover(self: ApplicationContext, modules):
        captured["discover_called"] = True

    monkeypatch.setattr(ApplicationContext, "build_consumer_registry", _build_consumer_registry)
    monkeypatch.setattr(ApplicationContext, "build_scenario", _build_scenario)
    monkeypatch.setattr(ApplicationContext, "discover", _discover)

    # Avoid adapter instantiation and execution; keep runtime path focused.
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.app.runtime._build_tracing", lambda _runtime: (None, None))
    monkeypatch.setattr(
        "stream_kernel.app.runtime.Runner",
        lambda **_kw: SimpleNamespace(run=lambda *_a, **_k: None),
    )
    monkeypatch.setattr("stream_kernel.app.runtime._emit_implicit_sink_diagnostics", lambda *_a, **_k: None)

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {
            "pipeline": ["a"],
            "discovery_modules": [],
        },
        "adapters": {"input_source": {"kind": "stub"}},
    }

    registry = AdapterRegistry()
    exit_code = run_with_config(
        config,
        adapter_registry=registry,
        adapter_bindings={},
        discovery_modules=[],
    )

    assert exit_code == 0
    assert captured.get("discover_called") is True
    assert captured.get("registry_built") is True
    assert captured.get("consumer_registry") is sentinel_registry
