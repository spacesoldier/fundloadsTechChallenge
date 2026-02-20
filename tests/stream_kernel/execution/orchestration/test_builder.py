from __future__ import annotations

import asyncio
import inspect
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
    build_runtime_observability_adapter_instances,
    build_runtime_artifacts,
    build_sink_runtime_nodes,
    build_injection_registry_from_bindings,
    execute_runtime_artifacts,
    ensure_platform_discovery_modules,
    ensure_runtime_observability_binding,
    ensure_runtime_api_policy_bindings,
    ensure_runtime_bootstrap_binding,
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
from stream_kernel.execution.orchestration.observability_system_nodes import (
    MetricDispatchEvent,
    TraceDispatchEvent,
    WorkerQueueTelemetryEvent,
    build_observability_system_plan,
)
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
from stream_kernel.application_context.inject import inject
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService
from stream_kernel.application_context.injection_registry import InjectionRegistry, InjectionRegistryError
from stream_kernel.execution.observers.observer_builder import build_execution_observers_from_factories
from stream_kernel.execution.observers.observer import ExecutionObserver, ObserverFactoryContext
from stream_kernel.execution.runtime.planning import plan_pools
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.application_context.application_context import ApplicationContext
from stream_kernel.integration.consumer_registry import ConsumerRegistry, InMemoryConsumerRegistry
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue, TcpLocalQueue
from stream_kernel.execution.transport.secure_tcp_transport import SecureTcpConfig, SecureTcpTransport
from stream_kernel.platform.services.observability import (
    FanoutObservabilityService,
    NoOpObservabilityService,
    ObservabilityPipelineService,
    ObservabilityService,
    ReplyAwareObservabilityService,
    WorkerQueueTelemetryService,
)
from stream_kernel.platform.services.api.policy import (
    ApiPolicyService,
    RateLimiterService,
)
from stream_kernel.platform.services.api.outbound import OutboundApiService
from stream_kernel.platform.services.messaging.reply_coordinator import (
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.runtime.transport import (
    MemoryRuntimeTransportService,
    RuntimeTransportService,
    TcpLocalRuntimeTransportService,
)
from stream_kernel.platform.services.messaging.reply_waiter import (
    InMemoryReplyWaiterService,
    TerminalEvent,
)
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager
from stream_kernel.platform.services.runtime.bootstrap import (
    BootstrapSupervisor,
    LocalBootstrapSupervisor,
    MultiprocessBootstrapSupervisor,
)
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


class _RunnerAutoToken:
    pass


class _AsyncServicePort:
    pass


class _RunnerAutoAsyncNode:
    # Async dependency marker used by auto-runner selection tests.
    stream = inject.stream(_RunnerAutoToken)

    def __call__(self, _payload: object, _ctx: object | None) -> list[object]:
        return []


class _RunnerAutoSyncNode:
    def __call__(self, _payload: object, _ctx: object | None) -> list[object]:
        return []


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


def test_build_runtime_observability_adapter_instances_builds_from_registry_and_forwards_backend() -> None:
    # OBS-K-B-01: runtime observability exporters must be materialized via AdapterRegistry only.
    registry = AdapterRegistry()
    captured: dict[str, object] = {}

    def _factory(settings: dict[str, object]) -> object:
        captured.update(settings)
        return object()

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {
                            "kind": "otel_otlp",
                            "backend": "requests",
                            "settings": {"endpoint": "http://collector:4318/v1/traces"},
                        }
                    ]
                }
            },
        },
        registry=registry,
    )

    assert "trace_otel_otlp#0" in instances
    assert "trace_otel_otlp" in instances
    assert captured["backend"] == "requests"
    assert captured["endpoint"] == "http://collector:4318/v1/traces"


def test_build_runtime_observability_adapter_instances_resolves_backend_from_transport_group() -> None:
    registry = AdapterRegistry()
    captured: dict[str, object] = {}

    def _factory(settings: dict[str, object]) -> object:
        captured.update(settings)
        return object()

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {
                            "kind": "otel_otlp",
                            "settings": {
                                "otlp": {"endpoint": "http://collector:4318/v1/traces"},
                                "transport": {"backend": "httpx", "httpx": {"mode": "async"}},
                            },
                        }
                    ]
                }
            },
        },
        registry=registry,
    )

    assert "trace_otel_otlp#0" in instances
    assert captured["backend"] == "httpx"
    # Grouped settings are passed through; adapter will normalize as needed.
    assert isinstance(captured.get("transport"), dict)
    assert isinstance(captured.get("otlp"), dict)


def test_build_runtime_observability_adapter_instances_maps_dual_view_kinds_to_trace_adapter() -> None:
    registry = AdapterRegistry()
    created: list[dict[str, object]] = []

    def _factory(settings: dict[str, object]) -> object:
        created.append(dict(settings))
        return object()

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {
                            "kind": "otel_otlp_logical",
                            "settings": {"endpoint": "http://collector:4318/v1/traces"},
                        },
                        {
                            "kind": "otel_otlp_topology",
                            "settings": {"endpoint": "http://collector:4318/v1/traces"},
                        },
                    ]
                }
            },
        },
        registry=registry,
    )

    assert "trace_otel_otlp#0" in instances
    assert "trace_otel_otlp#1" in instances
    assert created[0]["trace_view"] == "logical"
    assert created[0]["service_name_by_process_group"] is False
    assert created[0]["service_name_by_step"] is True
    assert created[0]["logical_include_platform_spans"] is False
    assert created[0]["service_name_suffix"] == ".logical"
    assert created[0]["isolate_view_ids"] is True
    assert created[1]["trace_view"] == "topology"
    assert created[1]["service_name_by_process_group"] is True
    assert created[1]["service_name_by_step"] is False
    assert created[1]["topology_include_business_spans"] is False
    assert created[1]["service_name_suffix"] == ".topology"
    assert created[1]["isolate_view_ids"] is True


def test_build_runtime_observability_adapter_instances_skips_disabled_tracing_exporter() -> None:
    registry = AdapterRegistry()

    def _factory(_settings: dict[str, object]) -> object:
        return object()

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)
    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {
                            "kind": "otel_otlp",
                            "enabled": False,
                            "settings": {"endpoint": "http://collector:4318/v1/traces"},
                        }
                    ]
                }
            },
        },
        registry=registry,
    )
    assert instances == {}


def test_build_runtime_observability_adapter_instances_raises_in_strict_mode_when_binding_missing() -> None:
    # OBS-K-B-02: strict mode must fail when runtime exporter adapter cannot be built from registry.
    with pytest.raises(ValueError, match="failed to build adapter 'trace_otel_otlp'"):
        build_runtime_observability_adapter_instances(
            runtime={
                "strict": True,
                "observability": {"tracing": {"exporters": [{"kind": "otel_otlp", "settings": {}}]}},
            },
            registry=AdapterRegistry(),
        )


def test_build_runtime_observability_adapter_instances_reports_deterministic_dependency_error() -> None:
    # OBS-K-E-05: strict startup error should include deterministic dependency diagnostics.
    registry = AdapterRegistry()

    def _factory(_settings: dict[str, object]) -> object:
        raise ValueError("trace_otel_otlp dependency missing for backend 'requests'")

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)

    with pytest.raises(ValueError, match="dependency missing for backend 'requests'"):
        build_runtime_observability_adapter_instances(
            runtime={
                "strict": True,
                "observability": {
                    "tracing": {
                        "exporters": [
                            {
                                "kind": "otel_otlp",
                                "backend": "requests",
                                "settings": {"endpoint": "http://collector:4318/v1/traces"},
                            }
                        ]
                    }
                },
            },
            registry=registry,
        )


def test_build_runtime_observability_adapter_instances_allows_explicit_degrade_mode() -> None:
    # OBS-K-E-06: explicit degrade mode should keep strict startup path deterministic and non-fatal.
    registry = AdapterRegistry()

    def _factory(settings: dict[str, object]) -> object:
        if settings.get("dependency_missing") == "degrade_noop":
            return object()
        raise ValueError("trace_otel_otlp dependency missing for backend 'requests'")

    registry.register("trace_otel_otlp", "trace_otel_otlp", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {
                            "kind": "otel_otlp",
                            "backend": "requests",
                            "settings": {
                                "endpoint": "http://collector:4318/v1/traces",
                                "dependency_missing": "degrade_noop",
                            },
                        }
                    ]
                }
            },
        },
        registry=registry,
    )

    assert "trace_otel_otlp#0" in instances


def test_build_runtime_observability_adapter_instances_skips_missing_binding_in_non_strict_mode() -> None:
    # OBS-K-B-03: non-strict mode may skip exporter adapters that are not registered.
    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": False,
            "observability": {"tracing": {"exporters": [{"kind": "otel_otlp", "settings": {}}]}},
        },
        registry=AdapterRegistry(),
    )
    assert instances == {}


def test_build_runtime_observability_adapter_instances_uses_indexed_keys_for_duplicate_exporter_kind() -> None:
    # OBS-K-B-04: duplicate exporters of same kind must stay addressable by index.
    registry = AdapterRegistry()
    created: list[object] = []

    def _factory(_settings: dict[str, object]) -> object:
        instance = object()
        created.append(instance)
        return instance

    registry.register("trace_stdout", "trace_stdout", _factory)
    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {"kind": "stdout", "settings": {}},
                        {"kind": "stdout", "settings": {}},
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances["trace_stdout#0"] is created[0]
    assert instances["trace_stdout#1"] is created[1]
    assert instances["trace_stdout"] is created[0]


def test_build_runtime_observability_adapter_instances_supports_stdout_plain_logging_exporter() -> None:
    # runtime.observability.logging.exporters.kind=stdout_plain should resolve via AdapterRegistry.
    registry = AdapterRegistry()
    created: list[object] = []

    def _factory(_settings: dict[str, object]) -> object:
        instance = object()
        created.append(instance)
        return instance

    registry.register("log_stdout_plain", "log_stdout_plain", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "logging": {
                    "exporters": [
                        {"kind": "stdout_plain", "settings": {}},
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances["log_stdout_plain#0"] is created[0]
    assert instances["log_stdout_plain"] is created[0]


def test_build_runtime_observability_adapter_instances_supports_file_plain_logging_exporter() -> None:
    # runtime.observability.logging.exporters.kind=file_plain should resolve via AdapterRegistry.
    registry = AdapterRegistry()
    created: list[object] = []

    def _factory(_settings: dict[str, object]) -> object:
        instance = object()
        created.append(instance)
        return instance

    registry.register("log_file_plain", "log_file_plain", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "observability": {
                "logging": {
                    "exporters": [
                        {"kind": "file_plain", "settings": {}},
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances["log_file_plain#0"] is created[0]
    assert instances["log_file_plain"] is created[0]


def test_build_runtime_observability_adapter_instances_skips_monitoring_exporters_for_worker_role() -> None:
    # monitoring sinks are supervisor-owned; worker runtime must not bind monitoring HTTP/textfile exporters.
    registry = AdapterRegistry()
    created: list[dict[str, object]] = []

    def _factory(settings: dict[str, object]) -> object:
        created.append(dict(settings))
        return object()

    registry.register("monitoring_prometheus", "monitoring_prometheus", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "__process_role": "worker",
            "strict": True,
            "observability": {
                "monitoring": {
                    "exporters": [
                        {
                            "kind": "prometheus",
                            "settings": {
                                "mode": "http_pull",
                                "http": {"host": "0.0.0.0", "port": 9465, "path": "/metrics"},
                            },
                        }
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances == {}
    assert created == []


def test_build_runtime_observability_adapter_instances_skips_tracing_exporters_for_worker_role() -> None:
    # Business workers must not instantiate tracing exporters directly.
    registry = AdapterRegistry()
    created: list[dict[str, object]] = []

    def _factory(settings: dict[str, object]) -> object:
        created.append(dict(settings))
        return object()

    registry.register("trace_jsonl", "trace_jsonl", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "__process_role": "worker",
            "strict": True,
            "observability": {
                "tracing": {
                    "exporters": [
                        {"kind": "jsonl", "settings": {"path": "traces/worker_trace.jsonl"}},
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances == {}
    assert created == []


def test_build_runtime_observability_adapter_instances_skips_monitoring_exporters_for_process_supervisor_mode() -> None:
    # In process_supervisor mode monitoring sinks are created only via supervisor.configure_monitoring().
    registry = AdapterRegistry()
    created: list[dict[str, object]] = []

    def _factory(settings: dict[str, object]) -> object:
        created.append(dict(settings))
        return object()

    registry.register("monitoring_prometheus", "monitoring_prometheus", _factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "monitoring": {
                    "exporters": [
                        {
                            "kind": "prometheus",
                            "settings": {
                                "mode": "http_pull",
                                "http": {"host": "0.0.0.0", "port": 9465, "path": "/metrics"},
                            },
                        }
                    ]
                }
            },
        },
        registry=registry,
    )

    assert instances == {}
    assert created == []


def test_build_runtime_observability_adapter_instances_allows_observability_worker_exports() -> None:
    # Dedicated observability worker owns tracing+monitoring adapters in process_supervisor topology.
    registry = AdapterRegistry()
    created_trace: list[dict[str, object]] = []
    created_monitoring: list[dict[str, object]] = []

    def _trace_factory(settings: dict[str, object]) -> object:
        created_trace.append(dict(settings))
        return object()

    def _monitoring_factory(settings: dict[str, object]) -> object:
        created_monitoring.append(dict(settings))
        return object()

    registry.register("trace_jsonl", "trace_jsonl", _trace_factory)
    registry.register("monitoring_prometheus", "monitoring_prometheus", _monitoring_factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "__process_role": "observability_worker",
            "strict": True,
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "tracing": {
                    "exporters": [
                        {"kind": "jsonl", "settings": {"path": "traces/obs_worker_trace.jsonl"}},
                    ]
                },
                "monitoring": {
                    "exporters": [
                        {
                            "kind": "prometheus",
                            "settings": {
                                "mode": "textfile",
                                "textfile": {"path": "metrics/obs_worker.prom"},
                            },
                        }
                    ]
                },
            },
        },
        registry=registry,
    )

    assert "trace_jsonl#0" in instances
    assert "monitoring_prometheus#0" in instances
    assert created_trace and created_monitoring


def test_build_runtime_observability_adapter_instances_skips_all_exporters_for_supervisor_when_service_process_enabled(
) -> None:
    # In dedicated service-process mode supervisor must stay transport-only
    # and should not instantiate tracing/logging/monitoring adapters.
    registry = AdapterRegistry()
    created_trace: list[dict[str, object]] = []
    created_log: list[dict[str, object]] = []
    created_monitoring: list[dict[str, object]] = []

    def _trace_factory(settings: dict[str, object]) -> object:
        created_trace.append(dict(settings))
        return object()

    def _log_factory(settings: dict[str, object]) -> object:
        created_log.append(dict(settings))
        return object()

    def _monitoring_factory(settings: dict[str, object]) -> object:
        created_monitoring.append(dict(settings))
        return object()

    registry.register("trace_jsonl", "trace_jsonl", _trace_factory)
    registry.register("log_jsonl", "log_jsonl", _log_factory)
    registry.register("monitoring_prometheus", "monitoring_prometheus", _monitoring_factory)

    instances = build_runtime_observability_adapter_instances(
        runtime={
            "strict": True,
            "platform": {"bootstrap": {"mode": "process_supervisor"}},
            "observability": {
                "service_process": {"enabled": True, "group_name": "system.observability"},
                "tracing": {
                    "exporters": [
                        {"kind": "jsonl", "settings": {"path": "traces/supervisor_trace.jsonl"}},
                    ]
                },
                "logging": {
                    "exporters": [
                        {"kind": "jsonl", "settings": {"path": "logs/supervisor_runtime.jsonl"}},
                    ]
                },
                "monitoring": {
                    "exporters": [
                        {"kind": "prometheus", "settings": {"mode": "textfile"}},
                    ]
                },
            },
        },
        registry=registry,
    )

    assert instances == {}
    assert created_trace == []
    assert created_log == []
    assert created_monitoring == []


def test_build_runtime_observability_adapter_instances_rejects_unknown_process_role_in_strict_mode() -> None:
    registry = AdapterRegistry()

    with pytest.raises(ValueError, match="runtime.__process_role"):
        build_runtime_observability_adapter_instances(
            runtime={
                "__process_role": "orchestrator",
                "strict": True,
                "observability": {
                    "tracing": {
                        "exporters": [
                            {"kind": "jsonl", "settings": {"path": "traces/invalid_role.jsonl"}},
                        ]
                    }
                },
            },
            registry=registry,
        )


def test_build_injection_registry_from_bindings_requires_instance() -> None:
    with pytest.raises(ValueError):
        build_injection_registry_from_bindings({}, {"source": [("stream", _StreamPort)]})


def test_build_injection_registry_from_bindings_marks_async_roles() -> None:
    registry = build_injection_registry_from_bindings(
        {"source": object()},
        {"source": [("stream", _StreamPort)]},
        adapter_async_roles={"source"},
    )
    assert registry.is_async_binding("stream", _StreamPort) is True


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


def test_register_discovered_services_marks_service_async_from_async_adapter_dependency() -> None:
    # Async adapter dependency should mark discovered service binding as async.
    module = ModuleType("fake.services.async_adapter")

    @service(name="async_dep")
    class _AsyncDependentService:
        dep = inject.stream(_AsyncServicePort, qualifier="external")

    module._AsyncDependentService = _AsyncDependentService

    registry = InjectionRegistry()
    registry.register_factory(
        "stream",
        _AsyncServicePort,
        lambda: object(),
        qualifier="external",
        is_async=True,
    )
    register_discovered_services(registry, [module])
    assert registry.is_async_binding("service", _AsyncDependentService) is True


def test_register_discovered_services_marks_transitive_service_async() -> None:
    # Service->service injection chain should propagate async capability transitively.
    module = ModuleType("fake.services.transitive_async")

    @service(name="leaf")
    class _LeafService:
        dep = inject.stream(_AsyncServicePort)

    @service(name="root")
    class _RootService:
        leaf = inject.service(_LeafService)

    module._LeafService = _LeafService
    module._RootService = _RootService

    registry = InjectionRegistry()
    registry.register_factory("stream", _AsyncServicePort, lambda: object(), is_async=True)
    register_discovered_services(registry, [module])
    assert registry.is_async_binding("service", _LeafService) is True
    assert registry.is_async_binding("service", _RootService) is True


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


def test_api_ing_01_source_ingress_rejects_when_web_limiter_exceeded() -> None:
    # API-ING-01: ingress message is rejected deterministically with status 429 when limiter denies.
    registry = AdapterRegistry()
    registry.register("events_source", "events_source", _source_factory)
    adapters = {"events_source": {"settings": {}}}
    adapter_instances = {
        "events_source": type(
            "I",
            (),
            {
                "read": lambda *_a: [
                    Envelope(payload={"request_id": 1}, reply_to="conn-a"),
                    Envelope(payload={"request_id": 2}, reply_to="conn-a"),
                ]
            },
        )()
    }
    injection = InjectionRegistry()
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    ensure_runtime_api_policy_bindings(
        injection_registry=injection,
        runtime={
            "platform": {},
            "web": {
                "interfaces": [
                    {
                        "kind": "http",
                        "binds": ["request", "response"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            },
        },
    )
    scope = injection.instantiate_for_scenario("s1")
    ingress = build_source_ingress_plan(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=registry,
        scenario_scope=scope,
        run_id="run",
        scenario_id="scenario",
        runtime={
            "web": {
                "interfaces": [
                    {
                        "kind": "http",
                        "binds": ["request", "response"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            }
        },
    )
    node = ingress.source_steps[0].step

    first = node({}, {})
    second = node({}, {})
    first_payloads = [item.payload for item in first if isinstance(item, Envelope) and isinstance(item.payload, dict)]
    second_payloads = [item.payload for item in second if isinstance(item, Envelope)]
    assert first_payloads == [{"request_id": 1}]
    assert any(isinstance(item, TerminalEvent) and item.status == "error" for item in second_payloads)
    terminal = next(item for item in second_payloads if isinstance(item, TerminalEvent))
    assert isinstance(terminal.payload, dict)
    assert terminal.payload.get("status_code") == 429
    assert terminal.payload.get("code") == "rate_limited"


def test_api_ing_02_source_ingress_uses_reply_to_as_rate_limit_key() -> None:
    # API-ING-02: limiter key is derived from reply_to so independent connections are isolated.
    registry = AdapterRegistry()
    registry.register("events_source", "events_source", _source_factory)
    adapters = {"events_source": {"settings": {}}}
    adapter_instances = {
        "events_source": type(
            "I",
            (),
            {
                "read": lambda *_a: [
                    Envelope(payload={"request_id": 1}, reply_to="conn-a"),
                    Envelope(payload={"request_id": 2}, reply_to="conn-b"),
                    Envelope(payload={"request_id": 3}, reply_to="conn-a"),
                ]
            },
        )()
    }
    injection = InjectionRegistry()
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    ensure_runtime_api_policy_bindings(
        injection_registry=injection,
        runtime={
            "platform": {},
            "web": {
                "interfaces": [
                    {
                        "kind": "websocket",
                        "binds": ["stream"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            },
        },
    )
    scope = injection.instantiate_for_scenario("s1")
    ingress = build_source_ingress_plan(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=registry,
        scenario_scope=scope,
        run_id="run",
        scenario_id="scenario",
        runtime={
            "web": {
                "interfaces": [
                    {
                        "kind": "websocket",
                        "binds": ["stream"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            }
        },
    )
    node = ingress.source_steps[0].step
    out1 = node({}, {})
    out2 = node({}, {})
    out3 = node({}, {})

    payloads1 = [item.payload for item in out1 if isinstance(item, Envelope) and isinstance(item.payload, dict)]
    payloads2 = [item.payload for item in out2 if isinstance(item, Envelope) and isinstance(item.payload, dict)]
    payloads3 = [item.payload for item in out3 if isinstance(item, Envelope)]
    assert payloads1 == [{"request_id": 1}]
    assert payloads2 == [{"request_id": 2}]
    assert any(isinstance(item, TerminalEvent) and item.status == "error" for item in payloads3)


def test_api_ing_03_source_ingress_emits_limiter_decision_observability_hook() -> None:
    # API-ING-03: allow/deny decisions are emitted through observability decision hook.
    decisions: list[dict[str, object]] = []

    class _Obs:
        def on_ingress_rate_limit_decision(self, **kwargs: object) -> None:
            decisions.append(dict(kwargs))

    registry = AdapterRegistry()
    registry.register("events_source", "events_source", _source_factory)
    adapters = {"events_source": {"settings": {}}}
    adapter_instances = {
        "events_source": type(
            "I",
            (),
            {
                "read": lambda *_a: [
                    Envelope(payload={"request_id": 1}, reply_to="conn-a"),
                    Envelope(payload={"request_id": 2}, reply_to="conn-a"),
                ]
            },
        )()
    }
    injection = InjectionRegistry()
    injection.register_factory("service", ContextService, lambda: InMemoryKvContextService(InMemoryKvStore()))
    injection.register_factory("service", ObservabilityService, lambda: _Obs())
    ensure_runtime_api_policy_bindings(
        injection_registry=injection,
        runtime={
            "platform": {},
            "web": {
                "interfaces": [
                    {
                        "kind": "http",
                        "binds": ["request", "response"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            },
        },
    )
    scope = injection.instantiate_for_scenario("s1")
    ingress = build_source_ingress_plan(
        adapters=adapters,
        adapter_instances=adapter_instances,
        adapter_registry=registry,
        scenario_scope=scope,
        run_id="run",
        scenario_id="scenario",
        runtime={
            "web": {
                "interfaces": [
                    {
                        "kind": "http",
                        "binds": ["request", "response"],
                        "policies": {
                            "rate_limit": {"kind": "fixed_window", "limit": 1, "window_ms": 60_000}
                        },
                    }
                ]
            }
        },
    )
    node = ingress.source_steps[0].step
    node({}, {})
    node({}, {})

    assert [item.get("allowed") for item in decisions] == [True, False]
    assert all(item.get("source_role") == "events_source" for item in decisions)


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


def test_ensure_runtime_bootstrap_binding_prefers_multiprocess_for_process_supervisor_mode() -> None:
    # Bootstrap supervisor must be selected by runtime.bootstrap.mode, not by service discovery order.
    registry = InjectionRegistry()
    ensure_runtime_bootstrap_binding(
        injection_registry=registry,
        runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}},
    )
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", BootstrapSupervisor)
    assert isinstance(resolved, MultiprocessBootstrapSupervisor)


def test_ensure_runtime_bootstrap_binding_uses_local_for_inline_mode() -> None:
    registry = InjectionRegistry()
    ensure_runtime_bootstrap_binding(
        injection_registry=registry,
        runtime={"platform": {"bootstrap": {"mode": "inline"}}},
    )
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", BootstrapSupervisor)
    assert isinstance(resolved, LocalBootstrapSupervisor)


def test_ensure_runtime_bootstrap_binding_defaults_to_multiprocess_when_groups_declared() -> None:
    registry = InjectionRegistry()
    ensure_runtime_bootstrap_binding(
        injection_registry=registry,
        runtime={"platform": {"process_groups": [{"name": "execution.cpu"}]}},
    )
    scope = registry.instantiate_for_scenario("s1")
    resolved = scope.resolve("service", BootstrapSupervisor)
    assert isinstance(resolved, MultiprocessBootstrapSupervisor)


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
    resolved_pipeline = scope.resolve("service", ObservabilityPipelineService)
    assert isinstance(resolved, ReplyAwareObservabilityService)
    assert isinstance(resolved_pipeline, ReplyAwareObservabilityService)
    assert isinstance(resolved.inner, FanoutObservabilityService)
    assert len(resolved.inner.observers) == 1


def test_ensure_runtime_observability_binding_marks_service_async_when_observer_requires_async() -> None:
    # OBS-ASYNC-BIND-01: async-capable observer callbacks should mark observability DI bindings as async.
    class _AsyncObserver(_Observer):
        async def on_trace_event_async(
            self,
            *,
            event: object,
            trace_id: str | None,
            attributes: dict[str, object] | None,
        ) -> None:
            _ = (event, trace_id, attributes)

    registry = InjectionRegistry()
    registry.register_factory(
        "service",
        ReplyCoordinatorService,
        lambda: legacy_reply_coordinator(reply_waiter=InMemoryReplyWaiterService(now_fn=lambda: 0)),
    )
    ensure_runtime_observability_binding(
        injection_registry=registry,
        observers=[_AsyncObserver()],
    )
    assert registry.is_async_binding("service", ObservabilityService)
    assert registry.is_async_binding("service", ObservabilityPipelineService)


def test_build_observability_system_plan_builds_enabled_nodes_and_consumers() -> None:
    class _PipelineRecorder(NoOpObservabilityService):
        def __init__(self) -> None:
            self.trace_calls = 0
            self.metric_calls = 0

        def publish_trace(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            _ = (event, trace_id, attributes)
            self.trace_calls += 1

        def publish_metric(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            _ = (event, trace_id, attributes)
            self.metric_calls += 1

    default_pipeline = _PipelineRecorder()
    qualified_pipeline = _PipelineRecorder()
    registry = InjectionRegistry()
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        lambda _svc=default_pipeline: _svc,
    )
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        lambda _svc=qualified_pipeline: _svc,
        qualifier="obs.async",
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "observability": {
                "pipeline": {
                    "system_nodes": [
                        {"kind": "system.obs.trace_dispatch", "enabled": True},
                        {"kind": "system.obs.log_dispatch", "enabled": False},
                        {"kind": "system.obs.metric_dispatch", "enabled": True, "qualifier": "obs.async"},
                    ]
                }
            }
        },
        scenario_scope=scope,
    )

    names = [step.name for step in plan.system_steps]
    assert names == ["system.obs.trace_dispatch", "system.obs.metric_dispatch:obs.async"]
    assert plan.system_consumers == {
        TraceDispatchEvent: ["system.obs.trace_dispatch"],
        MetricDispatchEvent: ["system.obs.metric_dispatch:obs.async"],
    }

    steps = {step.name: step.step for step in plan.system_steps}
    assert steps["system.obs.trace_dispatch"](TraceDispatchEvent(payload={"kind": "trace"}), {}) == []
    assert (
        steps["system.obs.metric_dispatch:obs.async"](
            MetricDispatchEvent(payload={"value": 1}),
            {},
        )
        == []
    )
    assert default_pipeline.trace_calls == 1
    assert qualified_pipeline.metric_calls == 1


def test_build_observability_system_plan_async_qualifier_propagates_to_pool_planning() -> None:
    registry = InjectionRegistry()
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        NoOpObservabilityService,
        is_async=False,
    )
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        NoOpObservabilityService,
        qualifier="obs.async",
        is_async=True,
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "observability": {
                "pipeline": {
                    "system_nodes": [
                        {"kind": "system.obs.trace_dispatch", "enabled": True},
                        {"kind": "system.obs.log_dispatch", "enabled": True, "qualifier": "obs.async"},
                        {"kind": "system.obs.metric_dispatch", "enabled": False},
                        {"kind": "system.obs.monitor_dispatch", "enabled": True},
                    ]
                }
            }
        },
        scenario_scope=scope,
    )
    nodes = {step.name: step.step for step in plan.system_steps}
    pools = plan_pools(nodes, registry)

    assert pools["system.obs.trace_dispatch"] == "sync"
    assert pools["system.obs.log_dispatch:obs.async"] == "async"
    assert pools["system.obs.monitor_dispatch"] == "sync"
    assert "system.obs.metric_dispatch" not in pools


def test_build_observability_system_plan_autowires_trace_dispatch_from_exporters() -> None:
    registry = InjectionRegistry()
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        NoOpObservabilityService,
        is_async=False,
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "observability": {
                "tracing": {
                    "exporters": [
                        {"kind": "otel_otlp", "enabled": True},
                    ]
                }
            }
        },
        scenario_scope=scope,
    )

    names = [step.name for step in plan.system_steps]
    assert names == ["system.obs.trace_dispatch"]
    assert plan.system_consumers == {TraceDispatchEvent: ["system.obs.trace_dispatch"]}


def test_build_observability_system_plan_autowires_worker_queue_dispatch_without_tracing_exporters() -> None:
    class _QueueTelemetryRecorder:
        def __init__(self) -> None:
            self.samples: list[object] = []

        def publish_sample(self, *, sample: object) -> None:
            self.samples.append(sample)

    registry = InjectionRegistry()
    recorder = _QueueTelemetryRecorder()
    registry.register_factory(
        "service",
        WorkerQueueTelemetryService,
        lambda _svc=recorder: _svc,
        is_async=False,
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "observability": {
                "worker_queue_telemetry": {"enabled": True, "sample_hz": 20},
            }
        },
        scenario_scope=scope,
    )

    names = [step.name for step in plan.system_steps]
    assert names == ["system.obs.worker_queue_dispatch"]
    assert plan.system_consumers == {WorkerQueueTelemetryEvent: ["system.obs.worker_queue_dispatch"]}

    steps = {step.name: step.step for step in plan.system_steps}
    event = WorkerQueueTelemetryEvent(
        group_name="execution.features",
        worker_id="execution.features#1",
        pid=12345,
        queue_depth=3,
        inflight=1,
        runner_profile="sync",
        ts_epoch_ms=123456789,
    )
    assert steps["system.obs.worker_queue_dispatch"](event, {}) == []
    assert recorder.samples and recorder.samples[-1] == event


def test_build_observability_system_plan_worker_queue_dispatch_prefers_async_service_in_event_loop() -> None:
    class _QueueTelemetryRecorder:
        def __init__(self) -> None:
            self.sync_samples: list[object] = []
            self.async_samples: list[object] = []

        def publish_sample(self, *, sample: object) -> None:
            self.sync_samples.append(sample)

        async def publish_sample_async(self, *, sample: object) -> None:
            self.async_samples.append(sample)

    registry = InjectionRegistry()
    recorder = _QueueTelemetryRecorder()
    registry.register_factory(
        "service",
        WorkerQueueTelemetryService,
        lambda _svc=recorder: _svc,
        is_async=True,
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "observability": {
                "worker_queue_telemetry": {"enabled": True, "sample_hz": 20},
            }
        },
        scenario_scope=scope,
    )

    steps = {step.name: step.step for step in plan.system_steps}
    event = WorkerQueueTelemetryEvent(
        group_name="execution.features",
        worker_id="execution.features#1",
        pid=12345,
        queue_depth=3,
        inflight=1,
        runner_profile="sync",
        ts_epoch_ms=123456789,
    )

    async def _invoke() -> None:
        produced = steps["system.obs.worker_queue_dispatch"](event, {})
        assert inspect.isawaitable(produced)
        await produced

    asyncio.run(_invoke())

    assert recorder.async_samples and recorder.async_samples[-1] == event
    assert recorder.sync_samples == []


def test_build_observability_system_plan_skips_worker_process_role_even_when_exporters_enabled() -> None:
    registry = InjectionRegistry()
    registry.register_factory(
        "service",
        ObservabilityPipelineService,
        NoOpObservabilityService,
        is_async=False,
    )
    scope = registry.instantiate_for_scenario("s1")

    plan = build_observability_system_plan(
        runtime={
            "__process_role": "worker",
            "observability": {
                "tracing": {
                    "exporters": [
                        {"kind": "otel_otlp", "enabled": True},
                    ]
                }
            }
        },
        scenario_scope=scope,
    )

    assert plan.system_steps == []
    assert plan.system_consumers == {}


def test_ensure_runtime_api_policy_bindings_registers_platform_services() -> None:
    # API-POL-DI-01: platform API policy service should be available through DI with runtime defaults/profiles.
    registry = InjectionRegistry()
    ensure_runtime_api_policy_bindings(
        injection_registry=registry,
        runtime={
            "platform": {
                "api_policies": {
                    "defaults": {
                        "timeout_ms": 2000,
                        "rate_limit": {
                            "kind": "token_bucket",
                            "refill_rate_per_sec": 20,
                            "bucket_capacity": 100,
                        },
                    },
                    "profiles": {
                        "partner_api": {
                            "timeout_ms": 1500,
                            "rate_limit": {
                                "kind": "fixed_window",
                                "limit": 50,
                                "window_ms": 1000,
                            },
                        }
                    },
                }
            }
        },
    )
    scope = registry.instantiate_for_scenario("s1")
    policy_service = scope.resolve("service", ApiPolicyService)
    assert policy_service.defaults().get("timeout_ms") == 2000
    partner = policy_service.profile("partner_api")
    assert partner.get("timeout_ms") == 1500
    assert partner.get("rate_limit", {}).get("kind") == "fixed_window"


def test_ensure_runtime_api_policy_bindings_registers_rate_limiter_qualifiers() -> None:
    # API-POL-DI-02: rate limiter service must be resolvable by qualifier/profile.
    registry = InjectionRegistry()
    ensure_runtime_api_policy_bindings(
        injection_registry=registry,
        runtime={
            "platform": {
                "api_policies": {
                    "defaults": {
                        "rate_limit": {
                            "kind": "token_bucket",
                            "refill_rate_per_sec": 10,
                            "bucket_capacity": 20,
                        }
                    },
                    "profiles": {
                        "partner_api": {
                            "rate_limit": {
                                "kind": "concurrency",
                                "max_in_flight": 5,
                            }
                        }
                    },
                }
            }
        },
    )
    scope = registry.instantiate_for_scenario("s1")
    default_limiter = scope.resolve("service", RateLimiterService)
    partner_limiter = scope.resolve("service", RateLimiterService, qualifier="partner_api")
    default_outbound = scope.resolve("service", OutboundApiService)
    partner_outbound = scope.resolve("service", OutboundApiService, qualifier="partner_api")
    assert default_limiter.config().get("kind") == "token_bucket"
    assert partner_limiter.config().get("kind") == "concurrency"
    assert default_outbound.policy().get("rate_limit", {}).get("kind") == "token_bucket"
    assert partner_outbound.policy().get("rate_limit", {}).get("kind") == "concurrency"


def test_ensure_runtime_api_policy_bindings_rejects_runner_profile_mismatch() -> None:
    # API-POL-DI-03: async-only policy profiles cannot be bound into sync runner groups.
    registry = InjectionRegistry()
    with pytest.raises(ValueError, match="requires runner_profile='async'"):
        ensure_runtime_api_policy_bindings(
            injection_registry=registry,
            runtime={
                "platform": {
                    "process_groups": [
                        {
                            "name": "execution.cpu",
                            "runner_profile": "sync",
                            "services": {"rate_limiter_profile": "partner_api"},
                        }
                    ],
                    "api_policies": {
                        "profiles": {
                            "partner_api": {
                                "execution_mode": "async",
                                "rate_limit": {"kind": "concurrency", "max_in_flight": 5},
                            }
                        }
                    },
                }
            },
        )


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


def test_execute_runtime_artifacts_selects_async_runner_from_injected_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RUN-AUTO-04: without explicit runner_profile, builder must infer async runner from DI contracts.
    sync_calls: list[dict[str, object]] = []
    async_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_sync_runner",
        lambda **kwargs: sync_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **kwargs: async_calls.append(dict(kwargs)),
    )

    injection_registry = InjectionRegistry()
    injection_registry.register_factory("stream", _RunnerAutoToken, lambda: object(), is_async=True)

    artifacts = RuntimeBuildArtifacts(
        scenario=SimpleNamespace(
            steps=[
                StepSpec(name="auto.async", step=_RunnerAutoAsyncNode()),
            ]
        ),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=InjectionRegistry().instantiate_for_scenario("scenario"),
        full_context_nodes=set(),
        injection_registry=injection_registry,
        runtime={
            "platform": {
                "process_groups": [
                    {"name": "execution.group"},
                ]
            }
        },
    )
    execute_runtime_artifacts(artifacts)

    assert sync_calls == []
    assert len(async_calls) == 1
    assert async_calls[0]["queue_qualifier"] == "execution.group"


def test_execute_runtime_artifacts_selects_sync_runner_when_dependencies_are_sync_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RUN-AUTO-05: default path must stay sync when no async DI dependencies are detected.
    sync_calls: list[dict[str, object]] = []
    async_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_sync_runner",
        lambda **kwargs: sync_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **kwargs: async_calls.append(dict(kwargs)),
    )

    injection_registry = InjectionRegistry()
    injection_registry.register_factory("stream", _RunnerAutoToken, lambda: object(), is_async=False)

    artifacts = RuntimeBuildArtifacts(
        scenario=SimpleNamespace(
            steps=[
                StepSpec(name="auto.sync", step=_RunnerAutoSyncNode()),
            ]
        ),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=InjectionRegistry().instantiate_for_scenario("scenario"),
        full_context_nodes=set(),
        injection_registry=injection_registry,
        runtime={
            "platform": {
                "process_groups": [
                    {"name": "execution.group"},
                ]
            }
        },
    )
    execute_runtime_artifacts(artifacts)

    assert len(sync_calls) == 1
    assert sync_calls[0]["queue_qualifier"] == "execution.group"
    assert async_calls == []


def test_execute_runtime_artifacts_explicit_runner_profile_overrides_auto_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RUN-AUTO-06: explicit runner_profile remains a hard override for exceptional cases.
    sync_calls: list[dict[str, object]] = []
    async_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_sync_runner",
        lambda **kwargs: sync_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "stream_kernel.execution.orchestration.builder.run_with_async_runner",
        lambda **kwargs: async_calls.append(dict(kwargs)),
    )

    injection_registry = InjectionRegistry()
    injection_registry.register_factory("stream", _RunnerAutoToken, lambda: object(), is_async=True)

    artifacts = RuntimeBuildArtifacts(
        scenario=SimpleNamespace(
            steps=[
                StepSpec(name="auto.async", step=_RunnerAutoAsyncNode()),
            ]
        ),
        inputs=[],
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=InjectionRegistry().instantiate_for_scenario("scenario"),
        full_context_nodes=set(),
        injection_registry=injection_registry,
        runtime={
            "platform": {
                "process_groups": [
                    {"name": "execution.group", "runner_profile": "sync"},
                ]
            }
        },
    )

    execute_runtime_artifacts(artifacts)

    assert len(sync_calls) == 1
    assert sync_calls[0]["queue_qualifier"] == "execution.group"
    assert async_calls == []


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
    adapters: dict[str, object] | None = None,
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
        adapters=dict(adapters or {}),
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


def test_execute_runtime_artifacts_process_supervisor_passes_adapter_config_to_child_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CHILD-BOOT-02: child bootstrap bundle must carry adapter config for runtime source/sink wrappers.
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
        adapters={
            "source": {"settings": {"path": "input.txt"}, "binds": ["stream"]},
            "sink": {"settings": {"path": "output.txt"}, "binds": ["stream"]},
        },
    )
    execute_runtime_artifacts(artifacts)

    bundle = captured.get("bundle")
    assert bundle is not None
    adapters = getattr(bundle, "adapters")
    assert isinstance(adapters, dict)
    assert "source" in adapters
    assert "sink" in adapters


def test_execute_runtime_artifacts_process_supervisor_passes_runtime_config_to_child_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CHILD-BOOT-03: child bootstrap bundle must carry full config for node-level config injection in worker.
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
    artifacts.config = {
        "runtime": artifacts.runtime,
        "nodes": {"compute_time_keys": {"week_start": "SUN"}},
    }

    execute_runtime_artifacts(artifacts)
    bundle = captured.get("bundle")
    assert bundle is not None
    assert getattr(bundle, "config") == artifacts.config


def test_execute_runtime_artifacts_process_supervisor_passes_routing_cache_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ROUTE-CACHE-01: process-supervisor path should pass runtime.platform.routing_cache into supervisor.
    captured: dict[str, object] = {}

    class _Supervisor:
        def configure_routing_cache(self, settings: dict[str, object]) -> None:
            captured["settings"] = dict(settings)

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
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "auth": {"mode": "hmac", "secret_mode": "generated", "kdf": "hkdf_sha256"},
                },
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
                "routing_cache": {"enabled": True, "negative_cache": True, "max_entries": 4096},
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)

    settings = captured.get("settings")
    assert settings == {"enabled": True, "negative_cache": True, "max_entries": 4096}


def test_execute_runtime_artifacts_process_supervisor_passes_lifecycle_logging_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOOT-LOG-01: process-supervisor path should pass runtime.observability.logging into supervisor.
    captured: dict[str, object] = {}

    class _Supervisor:
        def configure_lifecycle_logging(self, settings: dict[str, object]) -> None:
            captured["settings"] = dict(settings)

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
            "platform": {
                "execution_ipc": {
                    "transport": "tcp_local",
                    "auth": {"mode": "hmac", "secret_mode": "generated", "kdf": "hkdf_sha256"},
                },
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}],
            },
            "observability": {
                "logging": {
                    "exporters": [{"kind": "stdout"}],
                    "lifecycle_events": {"enabled": True, "level": "debug"},
                }
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)
    settings = captured.get("settings")
    assert settings == {
        "exporters": [{"kind": "stdout"}],
        "lifecycle_events": {"enabled": True, "level": "debug"},
    }


def test_execute_runtime_artifacts_process_supervisor_skips_supervisor_monitoring_when_service_process_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Supervisor:
        def configure_monitoring(self, settings: dict[str, object], *, strict: bool = True) -> None:
            captured["settings"] = dict(settings)
            captured["strict"] = strict

        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)
            return None

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "strict": True,
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}, {"name": "system.observability"}],
            },
            "observability": {
                "service_process": {"enabled": True, "group_name": "system.observability"},
                "monitoring": {
                    "exporters": [
                        {"kind": "prometheus", "settings": {"mode": "http_pull"}},
                    ]
                },
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)

    assert captured == {}


def test_execute_runtime_artifacts_process_supervisor_passes_lifecycle_logging_when_service_process_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Supervisor:
        def configure_lifecycle_logging(self, settings: dict[str, object]) -> None:
            captured["settings"] = dict(settings)

        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            return None

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)
            return None

    monkeypatch.setattr(builder_module, "run_with_sync_runner", lambda **_kwargs: None)
    artifacts = _runtime_artifacts_for_bootstrap_supervisor(
        runtime={
            "platform": {
                "execution_ipc": {"transport": "tcp_local"},
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [{"name": "execution.cpu"}, {"name": "system.observability"}],
            },
            "observability": {
                "service_process": {"enabled": True, "group_name": "system.observability"},
                "logging": {
                    "exporters": [{"kind": "stdout"}],
                    "lifecycle_events": {"enabled": True, "level": "debug"},
                },
            },
        },
        supervisor=_Supervisor(),
    )
    execute_runtime_artifacts(artifacts)

    assert captured == {
        "settings": {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        }
    }


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
