from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.execution.orchestration.builder import (
    BUILD_TIME_REGISTRY_TYPES,
    BootstrapControl,
    RUNTIME_SERVICE_REGISTRY_CONTRACTS,
    RuntimeBuildArtifacts,
    build_adapter_contracts,
    build_adapter_bindings,
    build_adapter_instances_from_registry,
    build_runtime_artifacts,
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
from stream_kernel.execution.orchestration.source_ingress import build_source_ingress_plan
from stream_kernel.execution.transport.bootstrap_keys import BootstrapChannelStateError
from stream_kernel.execution.orchestration.lifecycle_orchestration import (
    RuntimeBootstrapStopError,
    RuntimeBootstrapStopTimeoutError,
    RuntimeBootstrapStartError,
    RuntimeLifecycleReadyError,
    RuntimeLifecycleResolutionError,
    RuntimeWorkerFailedError,
)
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.context import ContextService, InMemoryKvContextService
from stream_kernel.application_context.injection_registry import InjectionRegistry, InjectionRegistryError
from stream_kernel.execution.observers.observer_builder import build_execution_observers_from_factories
from stream_kernel.execution.observers.observer import ExecutionObserver, ObserverFactoryContext
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue, TcpLocalQueue
from stream_kernel.execution.transport.secure_tcp_transport import SecureTcpConfig, SecureTcpTransport
from stream_kernel.platform.services.observability import (
    FanoutObservabilityService,
    NoOpObservabilityService,
    ObservabilityService,
    ReplyAwareObservabilityService,
)
from stream_kernel.platform.services.reply_coordinator import (
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.transport import (
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)
from stream_kernel.platform.services.reply_waiter import (
    InMemoryReplyWaiterService,
    TerminalEvent,
)
from stream_kernel.platform.services.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.bootstrap import BootstrapSupervisor
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult
from stream_kernel.kernel.dag import NodeContract, build_dag
import stream_kernel.execution.orchestration.builder as builder_module


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
    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.importlib.import_module", lambda _name: module)
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
    assert "stream_kernel.routing.routing_service" in modules
    assert "stream_kernel.observability.adapters" in modules
    assert "stream_kernel.observability.observers" in modules


def test_ensure_platform_discovery_modules_does_not_duplicate_entries() -> None:
    modules = [
        "fund_load.usecases.steps",
        "stream_kernel.integration.work_queue",
        "stream_kernel.routing.routing_service",
        "stream_kernel.observability.adapters",
        "stream_kernel.observability.observers",
    ]
    ensure_platform_discovery_modules(modules)
    assert modules.count("stream_kernel.integration.work_queue") == 1
    assert modules.count("stream_kernel.routing.routing_service") == 1
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
        RoutingService,
        lambda: RoutingService(registry=InMemoryConsumerRegistry({int: ["sink"]}), strict=True),
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
        RoutingService,
        lambda: RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
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


def test_runtime_transport_service_memory_profile_is_bound_in_di() -> None:
    # IPC-INT-09: runtime transport service must be available in DI for memory profile.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime={})
    scope = registry.instantiate_for_scenario("s1")
    transport = scope.resolve("service", RuntimeTransportService)
    assert isinstance(transport, MemoryRuntimeTransportService)
    assert transport.profile == "memory"


def test_runtime_transport_memory_profile_keeps_in_process_queue() -> None:
    # IPC-INT-01: no execution_ipc config keeps deterministic in-process queue transport.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime={})
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, InMemoryQueue)


def test_runtime_transport_service_tcp_local_profile_is_bound_in_di() -> None:
    # IPC-INT-10: runtime transport service must be available in DI for tcp_local profile.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(
        injection_registry=registry,
        runtime={
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
                    "max_payload_bytes": 1048576,
                }
            }
        },
    )
    scope = registry.instantiate_for_scenario("s1")
    transport = scope.resolve("service", RuntimeTransportService)
    assert isinstance(transport, TcpLocalRuntimeTransportService)
    assert transport.profile == "tcp_local"


def test_runtime_transport_tcp_local_generated_secret_mode_builds_signing_secret() -> None:
    # KEY-IPC-01: generated secret mode should provision non-empty signing secret bytes.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(
        injection_registry=registry,
        runtime={
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "auth": {
                        "mode": "hmac",
                        "secret_mode": "generated",
                        "kdf": "hkdf_sha256",
                        "ttl_seconds": 30,
                        "nonce_cache_size": 1000,
                    },
                    "max_payload_bytes": 1048576,
                }
            }
        },
    )
    scope = registry.instantiate_for_scenario("s1")
    transport = scope.resolve("service", RuntimeTransportService)
    assert isinstance(transport, TcpLocalRuntimeTransportService)
    secret = transport.transport.config.secret
    assert isinstance(secret, bytes)
    assert len(secret) > 0
    assert secret != b"runtime-session-secret"


def test_runtime_transport_secret_resolution_error_redacts_secret_value() -> None:
    # KEY-IPC-04: runtime transport errors must not leak secret material representations.
    class _SecretObject:
        def __repr__(self) -> str:  # pragma: no cover - representation only
            return "DO_NOT_LEAK_ME"

        __str__ = __repr__

    registry = InjectionRegistry()
    with pytest.raises(ValueError) as excinfo:
        ensure_runtime_transport_bindings(
            injection_registry=registry,
            runtime={
                "platform": {
                    "execution_ipc": {
                        "transport": "tcp_local",
                        "bind_host": "127.0.0.1",
                        "bind_port": 0,
                        "auth": {
                            "mode": "hmac",
                            "secret_mode": "static",
                            "kdf": "none",
                            "secret": _SecretObject(),
                            "ttl_seconds": 30,
                            "nonce_cache_size": 1000,
                        },
                        "max_payload_bytes": 1048576,
                    }
                }
            },
        )
    assert "DO_NOT_LEAK_ME" not in str(excinfo.value)


def test_runtime_transport_tcp_local_profile_selects_non_memory_queue() -> None:
    # IPC-INT-02: tcp_local profile should switch runtime queue wiring away from plain in-memory queue.
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(
        injection_registry=registry,
        runtime={
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
                    "max_payload_bytes": 1048576,
                }
            }
        },
    )
    scope = registry.instantiate_for_scenario("s1")
    transport = scope.resolve("service", RuntimeTransportService)
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    topic = scope.resolve("topic", Envelope, qualifier="execution.cpu")
    assert isinstance(transport, TcpLocalRuntimeTransportService)
    assert not isinstance(queue, InMemoryQueue)
    assert isinstance(queue, type(transport.build_queue()))
    assert isinstance(topic, type(transport.build_topic()))


def test_build_runtime_artifacts_accepts_tcp_local_profile_with_framework_lifecycle() -> None:
    # IPC-INT-04: tcp_local profile should build via framework lifecycle discovery without manual wiring.
    config = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "strict": True,
            "discovery_modules": [],
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
                    "max_payload_bytes": 1048576,
                }
            },
        },
        "nodes": {},
        "adapters": {},
    }

    artifacts = build_runtime_artifacts(config)
    assert artifacts.runtime["platform"]["execution_ipc"]["transport"] == "tcp_local"

    queue = artifacts.scenario_scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, TcpLocalQueue)

    # No-op scenario must execute through lifecycle-managed path without monkeypatch helpers.
    execute_runtime_artifacts(artifacts)


def test_runtime_tcp_local_rejects_invalid_signed_frame_before_enqueue() -> None:
    # IPC-INT-05: invalid signature must be rejected at queue boundary and never enqueued.
    runtime = {
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000, "secret": "phase2-secret"},
                "max_payload_bytes": 1024,
            }
        }
    }
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, TcpLocalQueue)

    signer = SecureTcpTransport(
        SecureTcpConfig(
            bind_host="127.0.0.1",
            bind_port=0,
            secret=b"phase2-secret",
            ttl_seconds=30,
            nonce_cache_size=1000,
            max_payload_bytes=1024,
            allowed_kinds={"event"},
        ),
        now_fn=lambda: 100,
    )
    signed = signer.sign_envelope(kind="event", payload_bytes=b"payload", trace_id="t-1", target="node-a")
    framed = signer.encode_framed_message(replace(signed, sig="deadbeef"))

    with pytest.raises(ValueError, match="tcp_local transport reject"):
        queue.push(framed)

    assert queue.pop() is None
    assert queue.transport_reject_count() == 1


def test_runtime_tcp_local_rejects_oversized_frame_and_counts_reject() -> None:
    # IPC-INT-06: oversized framed payload must be rejected at boundary and counted.
    runtime = {
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000, "secret": "phase2-secret"},
                "max_payload_bytes": 8,
            }
        }
    }
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, TcpLocalQueue)

    oversized_wire = (9).to_bytes(4, byteorder="big", signed=False) + b"x" * 9

    with pytest.raises(ValueError, match="tcp_local transport reject"):
        queue.push(oversized_wire)

    assert queue.transport_reject_count() == 1
    assert queue.pop() is None


def test_runtime_tcp_local_rejects_replay_nonce_in_queue_boundary() -> None:
    # IPC-INT-07: replayed nonce must be rejected in runtime queue path, not only unit transport tests.
    runtime = {
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000, "secret": "phase2-secret"},
                "max_payload_bytes": 1024,
            }
        }
    }
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, TcpLocalQueue)

    signer = SecureTcpTransport(
        SecureTcpConfig(
            bind_host="127.0.0.1",
            bind_port=0,
            secret=b"phase2-secret",
            ttl_seconds=30,
            nonce_cache_size=1000,
            max_payload_bytes=1024,
            allowed_kinds={"event"},
        )
    )
    signed = signer.sign_envelope(
        kind="event",
        payload_bytes=b"payload",
        trace_id="t-1",
        target="node-a",
        nonce="n-1",
    )
    framed = signer.encode_framed_message(signed)

    queue.push(framed)
    first = queue.pop()
    assert isinstance(first, Envelope)

    with pytest.raises(ValueError, match="tcp_local transport reject"):
        queue.push(framed)

    assert queue.transport_reject_count() == 1
    assert queue.pop() is None


def test_runtime_tcp_local_reject_diagnostics_do_not_leak_secret() -> None:
    # IPC-INT-08: transport secret must not appear in boundary error diagnostics.
    secret = "top-secret-never-leak"
    runtime = {
        "platform": {
            "execution_ipc": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000, "secret": secret},
                "max_payload_bytes": 8,
            }
        }
    }
    registry = InjectionRegistry()
    ensure_runtime_transport_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    queue = scope.resolve("queue", Envelope, qualifier="execution.cpu")
    assert isinstance(queue, TcpLocalQueue)

    oversized_wire = (9).to_bytes(4, byteorder="big", signed=False) + b"x" * 9
    with pytest.raises(ValueError) as excinfo:
        queue.push(oversized_wire)

    message = str(excinfo.value)
    assert secret not in message
    assert "secret" not in message.lower()


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
            "stream_kernel.routing.routing_service",
        ]
    )
    register_discovered_services(registry, modules)

    scope = registry.instantiate_for_scenario("s1")
    routing = scope.resolve("service", RoutingService)
    assert isinstance(routing, RoutingService)
    assert isinstance(routing.registry, ConsumerRegistry)


def test_ensure_runtime_observability_binding_uses_platform_fanout_service() -> None:
    # Runtime should bind reply-aware wrapper over platform observability fan-out service.
    registry = InjectionRegistry()
    observer = _Observer()
    registry.register_factory(
        "service",
        ReplyCoordinatorService,
        lambda: legacy_reply_coordinator(reply_waiter=InMemoryReplyWaiterService(now_fn=lambda: 0)),
    )
    ensure_runtime_observability_binding(
        injection_registry=registry,
        observers=[observer],
    )
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", ObservabilityService)
    assert isinstance(resolved, ReplyAwareObservabilityService)
    assert isinstance(resolved.inner, FanoutObservabilityService)
    assert len(resolved.inner.observers) == 1


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

    monkeypatch.setattr("stream_kernel.execution.orchestration.builder.run_with_sync_runner", _run_with_sync_runner)

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
        RoutingService,
        lambda: RoutingService(registry=InMemoryConsumerRegistry({}), strict=False),
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


def _runtime_artifacts_for_lifecycle(
    *,
    runtime: dict[str, object],
    lifecycle: object | None,
) -> RuntimeBuildArtifacts:
    injection = InjectionRegistry()
    if lifecycle is not None:
        injection.register_factory(
            "service",
            RuntimeLifecycleManager,
            lambda _lifecycle=lifecycle: _lifecycle,
        )
    scope = injection.instantiate_for_scenario("scenario")
    return RuntimeBuildArtifacts(
        scenario=type("S", (), {"steps": []})(),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
        runtime=runtime,
    )


def _runtime_artifacts_for_bootstrap_supervisor(
    *,
    runtime: dict[str, object],
    supervisor: object | None,
    reply_waiter: object | None = None,
) -> RuntimeBuildArtifacts:
    injection = InjectionRegistry()
    if supervisor is not None:
        injection.register_factory(
            "service",
            BootstrapSupervisor,
            lambda _supervisor=supervisor: _supervisor,
        )
    if reply_waiter is not None:
        injection.register_factory(
            "service",
            ReplyCoordinatorService,
            lambda _reply_waiter=reply_waiter: legacy_reply_coordinator(reply_waiter=_reply_waiter),
        )
    scope = injection.instantiate_for_scenario("scenario")
    return RuntimeBuildArtifacts(
        scenario=type("S", (), {"steps": []})(),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
        runtime=runtime,
    )


def test_execute_runtime_artifacts_starts_services_before_runner_for_tcp_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PROC-INT-01: lifecycle start/ready must happen before runner execution.
    events: list[str] = []

    class _Lifecycle:
        def start(self) -> None:
            events.append("start")

        def ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            events.append("ready")
            return True

        def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            events.append("stop")

    monkeypatch.setattr(
        builder_module,
        "run_with_sync_runner",
        lambda **_kwargs: events.append("runner"),
    )

    artifacts = _runtime_artifacts_for_lifecycle(
        runtime={
            "platform": {"execution_ipc": {"transport": "tcp_local"}},
        },
        lifecycle=_Lifecycle(),
    )
    execute_runtime_artifacts(artifacts)
    assert events == ["start", "ready", "runner", "stop"]


def test_execute_runtime_artifacts_requires_ready_before_runner_for_tcp_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PROC-INT-02: runner must not start until lifecycle reports ready.
    called_runner = {"value": False}

    class _Lifecycle:
        def start(self) -> None:
            return None

        def ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return False

        def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    def _runner(**_kwargs: object) -> None:
        called_runner["value"] = True

    monkeypatch.setattr(builder_module, "run_with_sync_runner", _runner)

    artifacts = _runtime_artifacts_for_lifecycle(
        runtime={
            "platform": {"execution_ipc": {"transport": "tcp_local"}},
        },
        lifecycle=_Lifecycle(),
    )
    with pytest.raises(RuntimeLifecycleReadyError, match="lifecycle ready"):
        execute_runtime_artifacts(artifacts)
    assert called_runner["value"] is False


def test_execute_runtime_artifacts_stops_with_graceful_drain_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PROC-INT-03: shutdown should pass graceful drain contract to lifecycle manager.
    stop_calls: list[tuple[int, bool]] = []

    class _Lifecycle:
        def start(self) -> None:
            return None

        def ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            stop_calls.append((graceful_timeout_seconds, drain_inflight))

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)

    artifacts = _runtime_artifacts_for_lifecycle(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "lifecycle": {"graceful_timeout_seconds": 7, "drain_inflight": True},
            },
        },
        lifecycle=_Lifecycle(),
    )
    execute_runtime_artifacts(artifacts)
    assert stop_calls == [(7, True)]


def test_execute_runtime_artifacts_wraps_worker_crash_with_runtime_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PROC-INT-04: worker crash should surface deterministic runtime error category.
    class _Lifecycle:
        def start(self) -> None:
            return None

        def ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    def _runner(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(builder_module, "run_with_sync_runner", _runner)

    artifacts = _runtime_artifacts_for_lifecycle(
        runtime={
            "platform": {"execution_ipc": {"transport": "tcp_local"}},
        },
        lifecycle=_Lifecycle(),
    )
    with pytest.raises(RuntimeWorkerFailedError, match="execution worker failed"):
        execute_runtime_artifacts(artifacts)


def test_execute_runtime_artifacts_requires_lifecycle_service_for_tcp_local() -> None:
    # PROC-INT-06: lifecycle service absence must raise deterministic resolution error.
    artifacts = _runtime_artifacts_for_lifecycle(
        runtime={
            "platform": {"execution_ipc": {"transport": "tcp_local"}},
        },
        lifecycle=None,
    )
    with pytest.raises(RuntimeLifecycleResolutionError, match="requires a registered RuntimeLifecycleManager"):
        execute_runtime_artifacts(artifacts)


def test_builder_does_not_expose_private_lifecycle_helpers_after_refactor() -> None:
    # PROC-INT-05: lifecycle orchestration should not depend on private builder helper points.
    assert not hasattr(builder_module, "_resolve_runtime_lifecycle_manager")
    assert not hasattr(builder_module, "_runtime_lifecycle_policy")


def test_execute_runtime_artifacts_process_supervisor_uses_group_lifecycle_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOOT-API-01: process_supervisor profile should call start_groups/wait_ready/stop_groups around runner execution.
    events: list[tuple[str, object]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            events.append(("start_groups", list(group_names)))

        def wait_ready(self, timeout_seconds: int) -> bool:
            events.append(("wait_ready", timeout_seconds))
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            events.append(("stop_groups", (graceful_timeout_seconds, drain_inflight)))

    monkeypatch.setattr(
        builder_module,
        "run_with_sync_runner",
        lambda **_kwargs: events.append(("runner", None)),
    )
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [
                    {"name": "web"},
                    {"name": "execution.cpu"},
                ],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert events == [
        ("start_groups", ["web", "execution.cpu"]),
        ("wait_ready", 5),
        ("runner", None),
        ("stop_groups", (10, True)),
    ]


def test_execute_runtime_artifacts_process_supervisor_preserves_group_order_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOOT-API-02: start_groups receives process_groups in declared runtime order.
    started: list[list[str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            started.append(list(group_names))

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [
                    {"name": "execution.asyncio"},
                    {"name": "execution.cpu"},
                    {"name": "execution.gpu"},
                ],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert started == [["execution.asyncio", "execution.cpu", "execution.gpu"]]


def test_execute_runtime_artifacts_process_supervisor_wraps_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOOT-API-03: start_groups failure should surface deterministic runtime bootstrap error category.
    called_runner = {"value": False}

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            raise RuntimeError("cannot start worker")

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    def _runner(**_kwargs: object) -> None:
        called_runner["value"] = True

    monkeypatch.setattr(builder_module, "run_with_sync_runner", _runner)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    with pytest.raises(RuntimeBootstrapStartError, match="bootstrap supervisor failed to start process groups"):
        execute_runtime_artifacts(artifacts)
    assert called_runner["value"] is False


def test_execute_runtime_artifacts_process_supervisor_prefers_boundary_executor_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PH4-D-01: when execute_boundary exists, process_supervisor path should use it instead of local runner callback.
    events: list[tuple[str, object]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            events.append(("start_groups", list(group_names)))

        def wait_ready(self, timeout_seconds: int) -> bool:
            events.append(("wait_ready", timeout_seconds))
            return True

        def execute_boundary(
            self,
            *,
            run,
            run_id: str,
            scenario_id: str,
            inputs: list[object],
        ) -> RoutingResult:
            _ = run
            events.append(("execute_boundary", (run_id, scenario_id, len(inputs))))
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            events.append(("stop_groups", (graceful_timeout_seconds, drain_inflight)))

    monkeypatch.setattr(
        builder_module,
        "run_with_sync_runner",
        lambda **_kwargs: events.append(("runner", None)),
    )
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "web"}, {"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert events == [
        ("start_groups", ["web", "execution.cpu"]),
        ("wait_ready", 5),
        ("execute_boundary", ("run", "scenario", 0)),
        ("stop_groups", (10, True)),
    ]


def test_execute_runtime_artifacts_process_supervisor_boundary_terminals_complete_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PH4-D-02: boundary terminal envelopes should complete reply waiters by trace id.
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
    waiter.register(trace_id="t1", reply_to="http:req-1", timeout_seconds=30)
    called_runner = {"value": False}

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(
            self,
            *,
            run,
            run_id: str,
            scenario_id: str,
            inputs: list[object],
        ) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[
                    Envelope(
                        payload=TerminalEvent(status="success", payload={"ok": True}),
                        trace_id="t1",
                        target="sink:ignored",
                    )
                ],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    def _runner(**_kwargs: object) -> None:
        called_runner["value"] = True

    monkeypatch.setattr(builder_module, "run_with_sync_runner", _runner)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "web"}, {"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
        reply_waiter=waiter,
    )
    execute_runtime_artifacts(artifacts)
    assert called_runner["value"] is False
    assert waiter.in_flight() == 0
    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"ok": True})


def test_execute_runtime_artifacts_process_supervisor_delivers_bootstrap_bundle_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # KEY-IPC-03: process-supervisor path should deliver bootstrap key bundle through one-shot channel.
    captured: dict[str, object] = {}

    class _Supervisor:
        _channel: object | None = None

        def load_bootstrap_channel(self, channel: object) -> None:
            self._channel = channel

        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            assert self._channel is not None
            receive = getattr(self._channel, "receive_once")
            bundle = receive()
            captured["created_at"] = getattr(bundle, "created_at_epoch")
            captured["secret_mode"] = bundle.execution_ipc.secret_mode
            captured["kdf"] = bundle.execution_ipc.kdf
            with pytest.raises(BootstrapChannelStateError):
                receive()

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "auth": {"mode": "hmac", "secret_mode": "generated", "kdf": "hkdf_sha256"},
                },
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert captured["secret_mode"] == "generated"
    assert captured["kdf"] == "hkdf_sha256"
    assert isinstance(captured["created_at"], int)


def test_execute_runtime_artifacts_process_supervisor_passes_child_bootstrap_metadata_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CHILD-BOOT-01: process-supervisor path should hand over metadata-only child bootstrap bundle.
    captured: dict[str, object] = {}

    class _Supervisor:
        def load_child_bootstrap_bundle(self, bundle: object) -> None:
            captured["bundle"] = bundle

        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return None

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "discovery_modules": ["fund_load"],
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "auth": {"mode": "hmac", "secret_mode": "generated", "kdf": "hkdf_sha256"},
                },
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)

    bundle = captured.get("bundle")
    assert bundle is not None
    assert getattr(bundle, "scenario_id") == "scenario"
    assert getattr(bundle, "discovery_modules") == ["fund_load"]
    assert isinstance(getattr(bundle, "runtime"), dict)
    assert getattr(bundle, "key_bundle").execution_ipc.secret_mode == "generated"
    # Metadata-only contract: no loaded module objects are passed through bundle.
    assert not hasattr(bundle, "modules")


def test_execute_runtime_artifacts_process_supervisor_graceful_stop_drains_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STOP-IPC-01: graceful stop path should pass timeout+drain contract and avoid forced terminate fallback.
    stop_calls: list[tuple[int, bool]] = []
    force_calls: list[list[str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> bool:
            stop_calls.append((graceful_timeout_seconds, drain_inflight))
            return True

        def force_terminate_groups(self, group_names: list[str]) -> None:
            force_calls.append(list(group_names))

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "lifecycle": {"graceful_timeout_seconds": 7, "drain_inflight": True},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert stop_calls == [(7, True)]
    assert force_calls == []


def test_execute_runtime_artifacts_process_supervisor_stop_timeout_forces_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STOP-IPC-02: stop timeout should trigger deterministic forced terminate fallback.
    force_calls: list[list[str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            raise TimeoutError("did not drain in time")

        def force_terminate_groups(self, group_names: list[str]) -> None:
            force_calls.append(list(group_names))

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}, {"name": "execution.asyncio"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert force_calls == [["execution.cpu", "execution.asyncio"]]


def test_execute_runtime_artifacts_process_supervisor_stop_timeout_without_force_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STOP-IPC-02b: if timeout fallback is unavailable, runtime should raise deterministic timeout category.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            raise TimeoutError("timeout")

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    with pytest.raises(RuntimeBootstrapStopTimeoutError, match="timed out"):
        execute_runtime_artifacts(artifacts)


def test_execute_runtime_artifacts_process_supervisor_emits_stop_events_once_per_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STOP-IPC-03: stop lifecycle events should be emitted exactly once per group.
    emitted: list[tuple[str, str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> bool:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            return True

        def emit_stop_event(self, *, group_name: str, mode: str) -> None:
            emitted.append((group_name, mode))

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "g1"}, {"name": "g2"}],
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    assert emitted.count(("g1", "graceful")) == 1
    assert emitted.count(("g2", "graceful")) == 1


def test_execute_runtime_artifacts_process_supervisor_force_terminate_failure_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STOP-IPC-02c: force terminate fallback failure should raise deterministic runtime stop error.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = graceful_timeout_seconds
            _ = drain_inflight
            raise TimeoutError("timeout")

        def force_terminate_groups(self, group_names: list[str]) -> None:
            _ = group_names
            raise RuntimeError("cannot terminate")

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
        },
        supervisor=_Supervisor(),
    )
    with pytest.raises(RuntimeBootstrapStopError, match="force terminate"):
        execute_runtime_artifacts(artifacts)
