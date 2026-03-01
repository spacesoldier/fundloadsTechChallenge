from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.injection_registry import InjectionRegistry
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


def test_runtime_wiring_handoff_bindings_register_dispatch_and_route_table_services() -> None:
    registry = InjectionRegistry()
    ipc = _IpcPort()
    router = _Router(groups={"remote.node": "execution.alpha"})
    registry.register_factory("kv_stream", ExecutionIpcKvStreamPort, lambda _ipc=ipc: _ipc)
    registry.register_factory("service", ProcessGroupRouterService, lambda _router=router: _router)

    ensure_runtime_ipc_handoff_bindings(injection_registry=registry)
    scope = registry.instantiate_for_scenario("s1")

    route_table = scope.resolve("service", ExecutionIpcRouteTableService)
    dispatch = scope.resolve("service", ExecutionIpcHandoffDispatchService)

    assert isinstance(route_table, InMemoryExecutionIpcRouteTableService)
    dispatched = dispatch.dispatch_envelope(
        Envelope(payload={"v": 1}, target="remote.node"),
        source_group="execution.root",
    )
    assert dispatched is True
    assert route_table.resolve_route(target="remote.node") == "execution.alpha#1"
    assert ipc.sends == [
        {"target_id": "execution.alpha#1", "payload": {"v": 1}, "no_reply": True}
    ]
