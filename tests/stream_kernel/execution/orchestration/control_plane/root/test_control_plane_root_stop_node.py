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
        tombstone_groups=(),
    )

    produced = node(Envelope(payload=payload, target="system.cp.root_stop"), None)

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    cancel_commands = [item for item in produced if isinstance(item, PlatformSchedulerCancelCommand)]
    assert len(stop_requests) == 1
    assert stop_requests[0].worker_id == "execution.ingress#1"
    assert stop_requests[0].command_id == "runtime-stop:execution.ingress#1"
    assert len(cancel_commands) == 5
    assert all(
        command.job_id.startswith("cp.root.leaf_ingress:source:system.cp.root_leaf_ingress:execution.ingress#1:")
        for command in cancel_commands
    )
    assert runner_control.requests == 1
