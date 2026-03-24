from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootStopNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
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


def test_root_stop_node_requests_runner_stop_only_after_all_issued_stop_requests_are_acked() -> None:
    runner_control = _RunnerControlStub()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneRootLeafStopRequestEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            command_id="runtime-stop:execution.ingress#1",
            reason="control_plane.leaf_drain_ready",
        )
    )
    state.append_event(
        ControlPlaneRootLeafStopRequestEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            command_id="runtime-stop:execution.policy#1",
            reason="control_plane.leaf_drain_ready",
        )
    )
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
    state.append_event(
        ControlPlaneShutdownReadyEvent(
            expected_groups=("execution.ingress", "execution.policy"),
            ready_groups=("execution.ingress", "execution.policy"),
        )
    )
    state.append_event(
        ControlPlaneLeafStopAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            command_id="runtime-stop:execution.ingress#1",
            status="accepted",
        )
    )
    node = ControlPlaneRootStopNode(
        state=state,  # type: ignore[arg-type]
        runner_control=runner_control,  # type: ignore[arg-type]
    )

    produced_not_ready = node(
        Envelope(
            payload=ControlPlaneLeafStopAckEvent(
                target_group="execution.ingress",
                worker_id="execution.ingress#1",
                command_id="runtime-stop:execution.ingress#1",
                status="accepted",
            ),
            target="system.cp.root_stop",
        ),
        None,
    )
    assert produced_not_ready == []
    assert runner_control.requests == 0

    state.append_event(
        ControlPlaneLeafStopAckEvent(
            target_group="execution.policy",
            worker_id="execution.policy#1",
            command_id="runtime-stop:execution.policy#1",
            status="accepted",
        )
    )
    produced = node(
        Envelope(
            payload=ControlPlaneLeafStopAckEvent(
                target_group="execution.policy",
                worker_id="execution.policy#1",
                command_id="runtime-stop:execution.policy#1",
                status="accepted",
            ),
            target="system.cp.root_stop",
        ),
        None,
    )

    stop_requests = [item for item in produced if isinstance(item, ControlPlaneRootLeafStopRequestEvent)]
    cancel_commands = [item for item in produced if isinstance(item, PlatformSchedulerCancelCommand)]
    assert stop_requests == []
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


def test_root_stop_node_does_not_stop_without_shutdown_ready_marker() -> None:
    runner_control = _RunnerControlStub()
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneRootLeafStopRequestEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            command_id="runtime-stop:execution.ingress#1",
            reason="control_plane.leaf_drain_ready",
        )
    )
    state.append_event(
        ControlPlaneLeafStopAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            command_id="runtime-stop:execution.ingress#1",
            status="accepted",
        )
    )
    node = ControlPlaneRootStopNode(
        state=state,  # type: ignore[arg-type]
        runner_control=runner_control,  # type: ignore[arg-type]
    )

    produced = node(
        Envelope(
            payload=ControlPlaneLeafStopAckEvent(
                target_group="execution.ingress",
                worker_id="execution.ingress#1",
                command_id="runtime-stop:execution.ingress#1",
                status="accepted",
            ),
            target="system.cp.root_stop",
        ),
        None,
    )

    assert produced == []
    assert runner_control.requests == 0
