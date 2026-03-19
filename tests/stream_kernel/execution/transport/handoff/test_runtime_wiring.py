from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)
from stream_kernel.execution.transport.handoff.runtime_wiring import (
    ensure_runtime_ipc_handoff_bindings,
    ensure_runtime_ipc_bindings,
)
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    ExecutionIpcRouteTableService,
    InMemoryExecutionIpcRouteTableService,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcKvStreamPort
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcTransportService
from stream_kernel.platform.services.runtime import ProcessGroupRouterService
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append(
            {
                "target_id": target_id,
                "payload": payload,
                "no_reply": no_reply,
            }
        )
        return None


@dataclass(slots=True)
class _Router:
    groups: dict[str, str]

    def resolve_group_for_target(self, *, target: str, source_group: str | None) -> str:
        _ = source_group
        return self.groups[target]


def test_runtime_wiring_ipc_bindings_default_to_pipe_adapter_in_process_supervisor() -> None:
    registry = InjectionRegistry()
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
            "execution_ipc": {"transport": "ipc_local"},
        }
    }

    ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")

    service = scope.resolve("service", ExecutionIpcTransportCoordinatorService)
    adapter = scope.resolve("kv_stream", ExecutionIpcKvStreamPort)

    assert isinstance(service, ExecutionIpcTransportCoordinatorService)
    assert isinstance(service.adapter, PipeExecutionIpcTransportAdapter)
    assert isinstance(adapter, PipeExecutionIpcTransportAdapter)


def test_runtime_wiring_applies_execution_ipc_payload_limits_to_pipe_adapter() -> None:
    registry = InjectionRegistry()
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
            "execution_ipc": {
                "transport": "ipc_local",
                "max_payload_bytes": 4096,
                "respect_pipe_capacity": False,
            },
        }
    }

    ensure_runtime_ipc_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    service = scope.resolve("service", ExecutionIpcTransportCoordinatorService)

    assert isinstance(service.adapter, PipeExecutionIpcTransportAdapter)
    assert service.adapter._max_payload_bytes == 4096
    assert service.adapter._respect_pipe_capacity is False


def test_runtime_wiring_handoff_bindings_register_dispatch_and_route_table_services() -> None:
    registry = InjectionRegistry()
    ipc = _IpcPort()
    router = _Router(groups={"remote.node": "execution.alpha"})
    registry.register_factory("kv_stream", ExecutionIpcKvStreamPort, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ExecutionIpcTransportService, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ProcessGroupRouterService, lambda _router=router: _router)

    ensure_runtime_ipc_handoff_bindings(injection_registry=registry)
    scope = registry.instantiate_for_scenario("s1")

    route_table = scope.resolve("service", ExecutionIpcRouteTableService)
    dispatch = scope.resolve("service", ExecutionIpcHandoffDispatchService)

    assert isinstance(route_table, InMemoryExecutionIpcRouteTableService)
    route_table.upsert_route(target="remote.node", target_id="execution.alpha#1")
    dispatched = dispatch.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )
    assert dispatched is True
    expected_envelope = Envelope(payload={"v": 1}, target="remote.node")
    assert ipc.sends == [
        {"target_id": "execution.alpha#1::data", "payload": expected_envelope, "no_reply": True}
    ]


def test_runtime_wiring_handoff_bindings_preload_observability_routes_from_runtime() -> None:
    registry = InjectionRegistry()
    ipc = _IpcPort()
    router = _Router(groups={})
    registry.register_factory("kv_stream", ExecutionIpcKvStreamPort, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ExecutionIpcTransportService, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ProcessGroupRouterService, lambda _router=router: _router)
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [
                {"name": "execution.ingress", "workers": 1, "nodes": ["source:source"]},
            ],
        },
        "observability": {
            "service_worker": {"enabled": True},
            "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
        },
    }

    ensure_runtime_ipc_handoff_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    route_table = scope.resolve("service", ExecutionIpcRouteTableService)
    dispatch = scope.resolve("service", ExecutionIpcHandoffDispatchService)

    assert route_table.resolve_route(target="source:source") == "execution.ingress#1"
    assert route_table.resolve_route(target="system.obs.trace_dispatch") == "system.observability#1"
    assert route_table.resolve_route(target="system.obs.log_dispatch") == "system.observability#1"
    dispatched = dispatch.dispatch_envelope(
        Envelope(payload={"trace": 1}, target="system.obs.trace_dispatch"),
        source_group="execution.ingress",
    )
    assert dispatched is True
    expected_envelope = Envelope(payload={"trace": 1}, target="system.obs.trace_dispatch")
    assert ipc.sends[-1] == {
        "target_id": "system.observability#1::trace",
        "payload": expected_envelope,
        "no_reply": True,
    }


def test_runtime_wiring_handoff_bindings_ring_worker_routes_business_direct_and_observability_by_source_lane() -> None:
    registry = InjectionRegistry()
    ipc = _IpcPort()
    router = _Router(groups={})
    registry.register_factory("kv_stream", ExecutionIpcKvStreamPort, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ExecutionIpcTransportService, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ProcessGroupRouterService, lambda _router=router: _router)
    runtime = {
        "__worker_id": "execution.ingress#1",
        "__process_group": "execution.ingress",
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "execution_ipc": {"data_plane_topology": "ring"},
            "process_groups": [
                {"name": "execution.ingress", "workers": 1, "nodes": ["pipeline.ingress"]},
                {"name": "execution.transform", "workers": 1, "nodes": ["pipeline.transform"]},
                {"name": "system.observability", "workers": 1, "nodes": ["system.obs.trace_dispatch"]},
            ],
        },
    }

    ensure_runtime_ipc_handoff_bindings(injection_registry=registry, runtime=runtime)
    scope = registry.instantiate_for_scenario("s1")
    route_table = scope.resolve("service", ExecutionIpcRouteTableService)
    dispatch = scope.resolve("service", ExecutionIpcHandoffDispatchService)

    assert route_table.resolve_route(target="pipeline.transform") == "ring:execution.ingress#1->execution.transform#1:data"
    assert route_table.resolve_route(target="system.obs.trace_dispatch") == "execution.ingress#1"

    business = Envelope(payload={"id": 1}, target="pipeline.transform")
    trace = Envelope(payload={"trace": 1}, target="system.obs.trace_dispatch")
    assert dispatch.dispatch_envelope(business, source_group="execution.ingress") is True
    assert dispatch.dispatch_envelope(trace, source_group="execution.ingress") is True

    assert ipc.sends == [
        {
            "target_id": "ring:execution.ingress#1->execution.transform#1:data::data",
            "payload": business,
            "no_reply": True,
        },
        {
            "target_id": "execution.ingress#1::trace",
            "payload": trace,
            "no_reply": True,
        },
    ]


def test_runtime_wiring_handoff_bindings_require_transport_service_binding() -> None:
    registry = InjectionRegistry()
    with pytest.raises(InjectionRegistryError, match="ExecutionIpcTransportService binding is required"):
        ensure_runtime_ipc_handoff_bindings(injection_registry=registry)
