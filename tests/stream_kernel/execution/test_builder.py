from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.execution.builder import (
    BUILD_TIME_REGISTRY_TYPES,
    BootstrapControl,
    RUNTIME_SERVICE_REGISTRY_CONTRACTS,
    RuntimeBuildArtifacts,
    build_adapter_contracts,
    build_adapter_bindings,
    build_adapter_instances_from_registry,
    build_sink_runtime_nodes,
    build_injection_registry_from_bindings,
    execute_runtime_artifacts,
    ensure_platform_discovery_modules,
    ensure_runtime_observability_binding,
    ensure_runtime_registry_bindings,
    ensure_runtime_transport_bindings,
    load_discovery_modules,
    initial_context,
    register_discovered_services,
    resolve_step_names,
    run_with_sync_runner,
    resolve_runtime_adapters,
    scenario_name,
    ensure_runtime_kv_binding,
    trace_id,
)
from stream_kernel.execution.source_ingress import build_source_ingress_plan
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.context import ContextService, InMemoryKvContextService
from stream_kernel.application_context.injection_registry import InjectionRegistry, InjectionRegistryError
from stream_kernel.execution.observer_builder import build_execution_observers_from_factories
from stream_kernel.execution.observer import ExecutionObserver, ObserverFactoryContext
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import (
    FanoutObservabilityService,
    NoOpObservabilityService,
    ObservabilityService,
)
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.routing.envelope import Envelope
from stream_kernel.kernel.dag import NodeContract, build_dag


class _Token:
    pass


class _OtherToken:
    pass


class _StreamPort:
    pass


class _KvPort:
    pass


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@adapter(name="source", kind="test.source", consumes=[], emits=[_Token], binds=[("stream", _StreamPort)])
def _source_factory(settings: dict[str, object]) -> object:
    return object()


def test_resolve_runtime_adapters_requires_mapping_role_config() -> None:
    with pytest.raises(ValueError):
        resolve_runtime_adapters(adapters={"sink": "nope"}, discovery_modules=[])  # type: ignore[arg-type]


def test_resolve_runtime_adapters_rejects_kind_in_yaml() -> None:
    with pytest.raises(ValueError):
        resolve_runtime_adapters(adapters={"sink": {"kind": "legacy"}}, discovery_modules=[])


def test_resolve_runtime_adapters_rejects_unknown_adapter_name(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("fake.adapters")
    module.source = _source_factory
    monkeypatch.setattr("stream_kernel.execution.builder.importlib.import_module", lambda _name: module)
    with pytest.raises(ValueError):
        resolve_runtime_adapters(adapters={"missing": {}}, discovery_modules=["fake.adapters"])


def test_build_adapter_bindings_requires_supported_port_type() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    with pytest.raises(ValueError):
        build_adapter_bindings(
            {"source": {"binds": ["kv"]}},
            registry,
        )


def test_build_adapter_bindings_resolves_typed_ports() -> None:
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    bindings = build_adapter_bindings(
        {"source": {"binds": ["stream"]}},
        registry,
    )
    assert bindings["source"] == [("stream", _StreamPort)]


def test_build_adapter_contracts_uses_role_name_as_contract_id() -> None:
    # Contract ids should be stable role names from config, without synthetic adapter prefixes.
    registry = AdapterRegistry()
    registry.register("source", "source", _source_factory)
    contracts = build_adapter_contracts({"source": {"settings": {}}}, adapter_registry=registry)
    assert len(contracts) == 1
    assert contracts[0].name == "source"
    assert contracts[0].external is True


def test_build_adapter_instances_from_registry_requires_mapping() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        build_adapter_instances_from_registry({"source": "nope"}, registry)  # type: ignore[arg-type]


def test_build_injection_registry_from_bindings_requires_instance() -> None:
    with pytest.raises(ValueError):
        build_injection_registry_from_bindings({}, {"source": [("stream", _StreamPort)]})


def test_ensure_platform_discovery_modules_appends_framework_modules() -> None:
    modules = ["fund_load.usecases.steps"]
    ensure_platform_discovery_modules(modules)
    assert "stream_kernel.integration.work_queue" in modules
    assert "stream_kernel.integration.routing_port" in modules
    assert "stream_kernel.observability.adapters" in modules
    assert "stream_kernel.observability.observers" in modules


def test_ensure_platform_discovery_modules_does_not_duplicate_entries() -> None:
    modules = [
        "fund_load.usecases.steps",
        "stream_kernel.integration.work_queue",
        "stream_kernel.integration.routing_port",
        "stream_kernel.observability.adapters",
        "stream_kernel.observability.observers",
    ]
    ensure_platform_discovery_modules(modules)
    assert modules.count("stream_kernel.integration.work_queue") == 1
    assert modules.count("stream_kernel.integration.routing_port") == 1
    assert modules.count("stream_kernel.observability.adapters") == 1
    assert modules.count("stream_kernel.observability.observers") == 1


def test_resolve_step_names_excludes_external_contract_nodes() -> None:
    # Execution plan should skip external contracts (adapter/platform nodes) by metadata, not by name hacks.
    dag = build_dag(
        [
            NodeContract(name="source", consumes=[], emits=[_Token]),
            NodeContract(name="adapter-input", consumes=[_Token], emits=[], external=True),
            NodeContract(name="sink", consumes=[_Token], emits=[]),
        ]
    )
    assert resolve_step_names(dag) == ["source", "sink"]


def test_load_discovery_modules_expands_package_root_recursively(
    tmp_path: Path,
) -> None:
    # Root package names should expand to all importable submodules recursively.
    pkg = tmp_path / "fake_root"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "mod_a.py", "x = 1\n")
    _write_file(pkg / "nested" / "__init__.py", "")
    _write_file(pkg / "nested" / "mod_b.py", "y = 2\n")

    sys.path.insert(0, str(tmp_path))
    try:
        modules = load_discovery_modules(["fake_root"])
    finally:
        sys.path.remove(str(tmp_path))

    names = [m.__name__ for m in modules]
    assert "fake_root" not in names
    assert "fake_root.mod_a" in names
    assert "fake_root.nested" in names
    assert "fake_root.nested.mod_b" in names


def test_load_discovery_modules_deduplicates_root_and_explicit_submodule(
    tmp_path: Path,
) -> None:
    # If both root and submodule are declared, each module should still appear once.
    pkg = tmp_path / "fake_root_dupe"
    _write_file(pkg / "__init__.py", "")
    _write_file(pkg / "mod_a.py", "x = 1\n")

    sys.path.insert(0, str(tmp_path))
    try:
        modules = load_discovery_modules(["fake_root_dupe", "fake_root_dupe.mod_a"])
    finally:
        sys.path.remove(str(tmp_path))

    names = [m.__name__ for m in modules]
    assert names.count("fake_root_dupe") == 0
    assert names.count("fake_root_dupe.mod_a") == 1


class _Observer:
    def before_node(self, **kwargs: object) -> object | None:
        return None

    def after_node(self, **kwargs: object) -> None:
        return None

    def on_node_error(self, **kwargs: object) -> None:
        return None

    def on_run_end(self) -> None:
        return None


def test_build_execution_observers_collects_from_factories() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> ExecutionObserver:
        return _Observer()

    observers = build_execution_observers_from_factories(
        factories={"x": _factory},
        runtime={},
        adapter_instances={},
        run_id="r1",
        scenario_id="s1",
        node_order=[],
    )
    assert len(observers) == 1


def test_build_execution_observers_flattens_list_result() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> list[ExecutionObserver]:
        return [_Observer(), _Observer()]

    observers = build_execution_observers_from_factories(
        factories={"x": _factory},
        runtime={},
        adapter_instances={},
        run_id="r1",
        scenario_id="s1",
        node_order=[],
    )
    assert len(observers) == 2


def test_build_execution_observers_rejects_non_observer_result() -> None:
    def _factory(_ctx: ObserverFactoryContext) -> object:
        return object()

    with pytest.raises(ValueError):
        build_execution_observers_from_factories(
            factories={"x": _factory},
            runtime={},
            adapter_instances={},
            run_id="r1",
            scenario_id="s1",
            node_order=[],
        )


def test_scenario_name_falls_back_when_missing() -> None:
    assert scenario_name({"scenario": "nope"}) == "scenario"


def test_trace_id_is_index_based_even_for_payload_with_line_no() -> None:
    class _Payload:
        line_no = 42

    # Runtime trace identity must be framework-generic and not depend on project fields.
    assert trace_id("run", _Payload(), 7) == "run:7"


def test_initial_context_does_not_include_payload_line_no() -> None:
    class _Payload:
        line_no = 42

    ctx = initial_context(_Payload(), "run:7", run_id="run", scenario_id="s")
    assert ctx == {
        "__trace_id": "run:7",
        "__run_id": "run",
        "__scenario_id": "s",
    }


def test_register_discovered_services_registers_service_contracts() -> None:
    # Runtime should register services discovered via @service markers.
    module = ModuleType("fake.services")

    @service(name="ctx")
    class _CustomContextService(ContextService):
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module._CustomContextService = _CustomContextService

    registry = InjectionRegistry()
    register_discovered_services(registry, [module])
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", ContextService)
    assert isinstance(resolved, _CustomContextService)


def test_register_discovered_services_keeps_existing_binding() -> None:
    # User/platform override must not be replaced by auto-discovered defaults.
    class _CustomContextService:
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module = ModuleType("fake.services")

    @service(name="ctx")
    class _DiscoveredContextService(ContextService):
        def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
            return None

        def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
            return {}

    module._DiscoveredContextService = _DiscoveredContextService

    registry = InjectionRegistry()
    custom = _CustomContextService()
    registry.register_factory("service", ContextService, lambda: custom)
    register_discovered_services(registry, [module])
    scope = registry.instantiate_for_scenario("s1")
    assert scope.resolve("service", ContextService) is custom


def test_build_source_ingress_plan_wraps_readable_adapters() -> None:
    # Source adapters should be converted into graph-native ingress runtime nodes.
    registry = AdapterRegistry()
    registry.register("events_source", "events_source", _source_factory)
    adapters = {"events_source": {"settings": {}}}
    adapter_instances = {"events_source": type("I", (), {"read": lambda *_a: [1, 2]})()}

    injection = InjectionRegistry()
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    scope = injection.instantiate_for_scenario("s1")

    ingress = build_source_ingress_plan(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=registry,
        scenario_scope=scope,
        run_id="run",
        scenario_id="scenario",
    )
    assert [step.name for step in ingress.source_steps] == ["source:events_source"]
    assert ingress.source_consumers == {BootstrapControl: ["source:events_source"]}
    assert ingress.source_node_names == {"source:events_source"}
    assert [item.target for item in ingress.bootstrap_inputs] == ["source:events_source"]
    node = ingress.source_steps[0].step
    first = node({}, {})
    second = node({}, {})
    third = node({}, {})
    assert [item.trace_id for item in first if isinstance(item.payload, int)] == ["run:events_source:1"]
    assert [item.payload for item in first if isinstance(item.payload, int)] == [1]
    assert [item.trace_id for item in second if isinstance(item.payload, int)] == ["run:events_source:2"]
    assert [item.payload for item in second if isinstance(item.payload, int)] == [2]
    assert third == []


def test_build_sink_runtime_nodes_wraps_consume_adapters_when_token_has_no_graph_consumer() -> None:
    # External sink adapters should become executable nodes when their token is not consumed in-graph.
    captured: list[object] = []

    class _SinkAdapter:
        def consume(self, payload: object) -> None:
            captured.append(payload)

    @adapter(name="sink_adapter", kind="sink_adapter", consumes=[_Token], emits=[])
    def _sink_factory(settings: dict[str, object]) -> object:
        _ = settings
        return _SinkAdapter()

    registry = AdapterRegistry()
    registry.register("sink_adapter", "sink_adapter", _sink_factory)
    adapters = {"sink_adapter": {"settings": {}}}
    adapter_instances = {"sink_adapter": _SinkAdapter()}

    nodes, consumers = build_sink_runtime_nodes(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=registry,
        in_graph_consumes=set(),
    )
    assert consumers == {_Token: ["sink:sink_adapter"]}
    assert "sink:sink_adapter" in nodes
    assert nodes["sink:sink_adapter"](_Token(), {}) == []
    assert len(captured) == 1
    assert isinstance(captured[0], _Token)


def test_run_with_sync_runner_executes_targeted_bootstrap_envelopes() -> None:
    # Source bootstrap now uses regular targeted Envelope inputs on the same execution path.
    seen: list[int] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
        return []

    source_node = lambda _payload, _ctx: [1]  # noqa: E731

    injection = InjectionRegistry()
    injection.register_factory("queue", Envelope, lambda: InMemoryQueue(), qualifier="execution.cpu")
    injection.register_factory(
        "service",
        RoutingPort,
        lambda: RoutingPort(registry=InMemoryConsumerRegistry({int: ["sink"]}), strict=True),
    )
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    injection.register_factory("service", ObservabilityService, NoOpObservabilityService)
    scope = injection.instantiate_for_scenario("s1")

    scenario = SimpleNamespace(
        steps=[
            StepSpec(name="source:events", step=source_node),
            StepSpec(name="sink", step=sink),
        ]
    )
    run_with_sync_runner(
        scenario=scenario,
        inputs=[Envelope(payload=BootstrapControl(target="source:events"), target="source:events")],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
    )
    assert seen == [1]


def test_run_with_sync_runner_bootstrap_targets_use_clean_control_payload() -> None:
    # Bootstrap control message should not leak marker payload structure into source node API.
    seen_payloads: list[object] = []

    def source_node(payload: object, _ctx: dict[str, object]) -> list[object]:
        seen_payloads.append(payload)
        return []

    injection = InjectionRegistry()
    injection.register_factory("queue", Envelope, lambda: InMemoryQueue(), qualifier="execution.cpu")
    injection.register_factory(
        "service",
        RoutingPort,
        lambda: RoutingPort(registry=InMemoryConsumerRegistry({}), strict=True),
    )
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    injection.register_factory("service", ObservabilityService, NoOpObservabilityService)
    scope = injection.instantiate_for_scenario("s1")

    scenario = SimpleNamespace(
        steps=[StepSpec(name="source:events", step=source_node)]
    )
    run_with_sync_runner(
        scenario=scenario,
        inputs=[Envelope(payload=BootstrapControl(target="source:events"), target="source:events")],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
    )
    assert len(seen_payloads) == 1
    assert isinstance(seen_payloads[0], BootstrapControl)
    assert seen_payloads[0].target == "source:events"


def test_ensure_runtime_kv_binding_registers_memory_backend_when_missing() -> None:
    # Runtime must provision default KV binding so services can inject KVStore without extra YAML boilerplate.
    registry = InjectionRegistry()
    ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "memory"}}})

    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("kv", KVStore)
    assert isinstance(resolved, InMemoryKvStore)


def test_ensure_runtime_kv_binding_keeps_existing_binding() -> None:
    # Explicit bindings win over runtime defaults.
    registry = InjectionRegistry()
    custom = object()
    registry.register_factory("kv", KVStore, lambda: custom)

    ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "memory"}}})
    scope = registry.instantiate_for_scenario("s1")
    assert scope.resolve("kv", KVStore) is custom


def test_ensure_runtime_transport_bindings_registers_queue_and_topic_ports() -> None:
    # Runtime must provide default queue/topic transport ports for sync execution.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime={})
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    topic = scope.resolve("topic", Envelope, qualifier="execution.cpu")
    assert hasattr(queue, "push")
    assert hasattr(queue, "pop")
    assert hasattr(topic, "publish")
    assert hasattr(topic, "consume")


def test_register_discovered_services_registers_routing_service() -> None:
    # Runtime routing should come from discovered framework services with ConsumerRegistry resolved via DI.
    registry = InjectionRegistry()
    ensure_runtime_registry_bindings(
        injection_registry=registry,
        app_context=ApplicationContext(),
    )
    ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "memory"}}})
    modules = load_discovery_modules(
        [
            "stream_kernel.platform.services",
            "stream_kernel.integration.work_queue",
            "stream_kernel.integration.routing_port",
        ]
    )
    register_discovered_services(registry, modules)

    scope = registry.instantiate_for_scenario("s1")
    routing = scope.resolve("service", RoutingPort)
    assert isinstance(routing, RoutingPort)
    assert isinstance(routing.registry, ConsumerRegistry)


def test_ensure_runtime_observability_binding_uses_platform_fanout_service() -> None:
    # Runtime should bind platform observability fan-out service, not execution shim.
    registry = InjectionRegistry()
    observer = _Observer()
    ensure_runtime_observability_binding(
        injection_registry=registry,
        observers=[observer],
    )
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", ObservabilityService)
    assert isinstance(resolved, FanoutObservabilityService)
    assert len(resolved.observers) == 1


def test_ensure_runtime_kv_binding_rejects_unknown_backend() -> None:
    # Unsupported backend must fail fast during runtime bootstrap.
    registry = InjectionRegistry()
    with pytest.raises(ValueError):
        ensure_runtime_kv_binding(registry, {"platform": {"kv": {"backend": "redis"}}})


def test_execute_runtime_artifacts_delegates_to_sync_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime orchestration should execute via a single builder API call.
    captured: dict[str, object] = {}

    def _run_with_sync_runner(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("stream_kernel.execution.builder.run_with_sync_runner", _run_with_sync_runner)

    artifacts = RuntimeBuildArtifacts(
        scenario=type("S", (), {"steps": []})(),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=InjectionRegistry().instantiate_for_scenario("scenario"),
        full_context_nodes=set(),
    )
    execute_runtime_artifacts(artifacts)
    assert captured["run_id"] == "run"
    assert captured["scenario_id"] == "scenario"
    assert captured["strict"] is True


def test_run_with_sync_runner_closes_scenario_scope_after_execution() -> None:
    # Scenario scope lifecycle should be finalized after execution.
    injection = InjectionRegistry()
    injection.register_factory("queue", Envelope, lambda: InMemoryQueue(), qualifier="execution.cpu")
    injection.register_factory(
        "service",
        RoutingPort,
        lambda: RoutingPort(registry=InMemoryConsumerRegistry({}), strict=False),
    )
    injection.register_factory("kv", KVStore, lambda: InMemoryKvStore())
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    injection.register_factory("service", ObservabilityService, NoOpObservabilityService)
    scope = injection.instantiate_for_scenario("s1")

    scenario = type("S", (), {"steps": []})()
    run_with_sync_runner(
        scenario=scenario,
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
    )

    with pytest.raises(InjectionRegistryError, match="ScenarioScope is closed"):
        scope.resolve("queue", Envelope, qualifier="execution.cpu")


def test_registry_role_partition_is_explicit() -> None:
    # Build-time registries and runtime service registries must stay separated by contract.
    assert AdapterRegistry in BUILD_TIME_REGISTRY_TYPES
    assert InjectionRegistry in BUILD_TIME_REGISTRY_TYPES
    assert ApplicationContext in RUNTIME_SERVICE_REGISTRY_CONTRACTS
    assert ApplicationContext not in BUILD_TIME_REGISTRY_TYPES
