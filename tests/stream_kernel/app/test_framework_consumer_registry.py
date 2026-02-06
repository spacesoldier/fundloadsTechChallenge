from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import run_with_config
import stream_kernel.app.runtime as runtime_module
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
        "stream_kernel.app.runtime.SyncRunner",
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


def test_run_with_config_uses_sync_runner_when_tracing_disabled(monkeypatch) -> None:
    # Runtime should execute via SyncRunner on the non-tracing path.
    captured: dict[str, object] = {"sync_used": False}

    monkeypatch.setattr(ApplicationContext, "build_consumer_registry", lambda self: InMemoryConsumerRegistry())
    monkeypatch.setattr(
        ApplicationContext,
        "build_scenario",
        lambda self, *, scenario_id, step_names, wiring: SimpleNamespace(
            steps=[
                SimpleNamespace(
                    name="a",
                    step=lambda msg, ctx: [],
                )
            ]
        ),
    )
    monkeypatch.setattr(ApplicationContext, "discover", lambda self, modules: None)
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.app.runtime._build_tracing", lambda _runtime: (None, None))
    monkeypatch.setattr("stream_kernel.app.runtime._emit_implicit_sink_diagnostics", lambda *_a, **_k: None)
    assert not hasattr(runtime_module, "Runner")

    def _sync_runner(**_kw):
        captured["sync_used"] = True
        return SimpleNamespace(run=lambda: None)

    monkeypatch.setattr("stream_kernel.app.runtime.SyncRunner", _sync_runner)

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {"pipeline": ["a"], "discovery_modules": []},
        "adapters": {"input_source": {"kind": "stub"}},
    }

    exit_code = run_with_config(
        config,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["sync_used"] is True


def test_run_with_config_uses_sync_runner_when_tracing_enabled(monkeypatch) -> None:
    # Runtime should execute via SyncRunner even when tracing is enabled.
    captured: dict[str, object] = {"sync_used": False}

    monkeypatch.setattr(ApplicationContext, "build_consumer_registry", lambda self: InMemoryConsumerRegistry())
    monkeypatch.setattr(
        ApplicationContext,
        "build_scenario",
        lambda self, *, scenario_id, step_names, wiring: SimpleNamespace(
            steps=[
                SimpleNamespace(
                    name="a",
                    step=lambda msg, ctx: [],
                )
            ]
        ),
    )
    monkeypatch.setattr(ApplicationContext, "discover", lambda self, modules: None)
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.app.runtime._build_injection_registry_from_bindings",
        lambda _instances, _bindings: InjectionRegistry(),
    )
    monkeypatch.setattr("stream_kernel.app.runtime._emit_implicit_sink_diagnostics", lambda *_a, **_k: None)
    assert not hasattr(runtime_module, "Runner")

    def _sync_runner(**_kw):
        captured["sync_used"] = True
        return SimpleNamespace(run=lambda: None)

    monkeypatch.setattr("stream_kernel.app.runtime.SyncRunner", _sync_runner)

    class _Sink:
        def emit(self, _record) -> None:
            return None

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("stream_kernel.app.runtime._build_tracing", lambda _runtime: (object(), _Sink()))

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {"pipeline": ["a"], "discovery_modules": [], "tracing": {"enabled": True}},
        "adapters": {"input_source": {"kind": "stub"}},
    }

    exit_code = run_with_config(
        config,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["sync_used"] is True
