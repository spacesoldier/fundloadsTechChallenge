from __future__ import annotations

from types import SimpleNamespace

from fund_load.domain.messages import RawLine
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.runtime import run_with_config
from stream_kernel.execution.orchestration.builder import run_with_sync_runner
from stream_kernel.execution.orchestration.builder import (
    ensure_runtime_control_plane_discovery_bindings,
    load_discovery_modules,
    register_discovered_services,
    ensure_runtime_registry_bindings,
    ensure_runtime_ipc_bindings,
    ensure_runtime_transport_bindings,
)
import stream_kernel.app.runtime as runtime_module
from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService, ObservabilityService
from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


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
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings, **_kwargs: InjectionRegistry(),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **_kw: None,
    )

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": [],
        },
        "adapters": {"input_source": {}},
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


def test_run_with_config_uses_async_runner_when_tracing_disabled(monkeypatch) -> None:
    # Runtime should execute via AsyncRunner on the non-tracing path.
    captured: dict[str, object] = {"async_used": False}

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
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings, **_kwargs: InjectionRegistry(),
    )
    assert not hasattr(runtime_module, "Runner")

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **_kw: captured.__setitem__("async_used", True),
    )

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {"input_source": {}},
    }

    exit_code = run_with_config(
        config,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["async_used"] is True


def test_run_with_config_uses_async_runner_when_tracing_enabled(monkeypatch) -> None:
    # Runtime should execute via AsyncRunner even when tracing is enabled.
    captured: dict[str, object] = {"async_used": False}

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
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings, **_kwargs: InjectionRegistry(),
    )
    assert not hasattr(runtime_module, "Runner")

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **_kw: captured.__setitem__("async_used", True),
    )

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": [], "tracing": {"enabled": True}},
        "adapters": {"input_source": {}},
    }

    exit_code = run_with_config(
        config,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["async_used"] is True


def test_runtime_bootstrap_routes_input_by_token_not_first_step() -> None:
    # Initial payloads should be routed by ConsumerRegistry token mapping, not first scenario step.
    seen: dict[str, bool] = {"parse": False}

    def sink(msg: object, ctx: dict[str, object]) -> list[object]:
        raise AssertionError("sink must not receive RawLine bootstrap input")

    def parse(msg: object, ctx: dict[str, object]) -> list[object]:
        assert isinstance(msg, RawLine)
        seen["parse"] = True
        return []

    scenario = SimpleNamespace(
        steps=[
            StepSpec(name="sink", step=sink),
            StepSpec(name="parse", step=parse),
        ]
    )
    registry = InMemoryConsumerRegistry()
    registry.register(RawLine, ["parse"])
    injection = InjectionRegistry()
    app_context = ApplicationContext()
    app_context.nodes = []
    injection.register_factory("kv", KVStore, lambda: InMemoryKvStore())
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    injection.register_factory("service", ObservabilityService, NoOpObservabilityService)
    ensure_runtime_registry_bindings(injection_registry=injection, app_context=app_context)
    ensure_runtime_control_plane_discovery_bindings(injection_registry=injection, runtime={})
    ensure_runtime_transport_bindings(injection_registry=injection, runtime={})
    ensure_runtime_ipc_bindings(injection_registry=injection, runtime={})
    injection.register_factory("service", ConsumerRegistry, lambda _r=registry: _r)
    register_discovered_services(
        injection,
        load_discovery_modules(
            [
                "stream_kernel.platform.services",
                "stream_kernel.integration.work_queue",
                "stream_kernel.routing.routing_service",
            ]
        ),
    )

    run_with_sync_runner(
        scenario=scenario,
        inputs=[RawLine(line_no=1, raw_text='{"id":"1"}')],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=injection.instantiate_for_scenario("scenario"),
    )
    assert seen["parse"] is True


def test_run_with_config_uses_di_context_service_and_reuses_scenario_scope(monkeypatch) -> None:
    # Runner should resolve ContextService via DI scope created before scenario assembly.
    captured: dict[str, object] = {}

    class _CustomContextService:
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    custom_service = _CustomContextService()
    registry = InjectionRegistry()
    registry.register_factory("service", ContextService, lambda: custom_service)

    monkeypatch.setattr(ApplicationContext, "build_consumer_registry", lambda self: InMemoryConsumerRegistry())

    def _build_scenario(self: ApplicationContext, *, scenario_id, step_names, wiring):
        captured["scenario_scope"] = wiring.get("scenario_scope")
        return SimpleNamespace(steps=[])

    monkeypatch.setattr(ApplicationContext, "build_scenario", _build_scenario)
    monkeypatch.setattr(ApplicationContext, "discover", lambda self, modules: None)
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_adapter_instances_from_registry",
        lambda _adapters, _registry: {"input_source": SimpleNamespace(read=lambda: [])},
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.build_injection_registry_from_bindings",
        lambda _instances, _bindings, **_kwargs: registry,
    )
    def _runner(**kwargs: object) -> None:
        scope = kwargs["scenario_scope"]
        captured["runner_context_service"] = scope.resolve("service", ContextService)
        captured["runner_scope"] = scope

    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_async_runner", _runner)

    config = {
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": []},
        "adapters": {"input_source": {}},
    }

    exit_code = run_with_config(
        config,
        adapter_registry=AdapterRegistry(),
        adapter_bindings={},
        discovery_modules=[],
    )
    assert exit_code == 0
    assert captured["runner_context_service"] is custom_service
    assert captured["scenario_scope"] is not None
    assert captured["runner_scope"] is captured["scenario_scope"]
