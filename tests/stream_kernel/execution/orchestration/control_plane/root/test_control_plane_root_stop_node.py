from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootStopNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneRootLeafStopRequestEvent,
    ControlPlaneShutdownReadyEvent,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _RunnerControlStub:
    requests: int = 0

    def request_stop(self) -> None:
        self.requests += 1


def test_root_stop_node_emits_leaf_stop_requests_and_requests_runner_stop() -> None:
    runner_control = _RunnerControlStub()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        {
            "kind": "control_plane.lifecycle.worker_spawned",
            "group_name": "execution.ingress",
            "worker_id": "execution.ingress#1",
        }
    )
    state.append_event(
        {
            "kind": "control_plane.lifecycle.worker_spawned",
            "group_name": "execution.policy",
            "worker_id": "execution.policy#1",
        }
    )
    node = ControlPlaneRootStopNode(
        state=state,  # type: ignore[arg-type]
        runner_control=runner_control,  # type: ignore[arg-type]
    )
    payload = ControlPlaneShutdownReadyEvent(
        expected_groups=("execution.ingress",),
        ready_groups=("execution.ingress",),
    )

    produced = node(Envelope(payload=payload, target="system.cp.root_stop"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    cancel_commands = [item for item in produced if isinstance(item, PlatformSchedulerCancelCommand)]
    assert sorted(item.worker_id for item in stop_requests) == [
        "execution.ingress#1",
        "execution.policy#1",
    ]
    assert sorted(item.command_id for item in stop_requests) == [
        "runtime-stop:execution.ingress#1",
        "runtime-stop:execution.policy#1",
    ]
    assert len(cancel_commands) == 4
    assert {
        command.job_id
        for command in cancel_commands
    } == {
        "cp.root.leaf_ingress:source:system.cp.root_leaf_ingress:execution.ingress#1:control",
        "cp.root.leaf_ingress:source:system.cp.root_leaf_ingress:execution.ingress#1:data",
        "cp.root.leaf_ingress:source:system.cp.root_leaf_ingress:execution.policy#1:control",
        "cp.root.leaf_ingress:source:system.cp.root_leaf_ingress:execution.policy#1:data",
    }
    assert runner_control.requests == 1
