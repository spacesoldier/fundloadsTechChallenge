from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import ControlPlaneStartWorkDispatchNode
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneStartWorkEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _BoundaryExec:
    calls: list[dict[str, object]] = field(default_factory=list)

    def execute_boundary_on_leaf(
        self,
        *,
        target_group: str,
        worker_id: str,
        request_id: str,
        inputs: tuple[object, ...],
        timeout_seconds: float,
        finalize: bool = True,
        wait_for_result: bool = True,
    ):
        self.calls.append(
            {
                "target_group": target_group,
                "worker_id": worker_id,
                "request_id": request_id,
                "inputs": inputs,
                "timeout_seconds": timeout_seconds,
                "finalize": finalize,
                "wait_for_result": wait_for_result,
            }
        )
        class _Routing:
            terminal_outputs: list[object] = []
        return _Routing()


@dataclass(slots=True)
class _HandoffDispatch:
    calls: list[dict[str, object]] = field(default_factory=list)

    def dispatch_broadcast(
        self,
        envelope: object,
        *,
        target_group: str | None = None,
        include_observability: bool = False,
        policy: str = "best_effort",
        broadcast_id: str | None = None,
    ):
        self.calls.append(
            {
                "envelope": envelope,
                "target_group": target_group,
                "include_observability": include_observability,
                "policy": policy,
                "broadcast_id": broadcast_id,
            }
        )

        @dataclass(frozen=True, slots=True)
        class _Result:
            broadcast_id: str
            policy: str
            total: int
            accepted: int
            failed: int
            failed_workers: tuple[str, ...] = ()

        return _Result(
            broadcast_id=broadcast_id or "broadcast:test",
            policy=policy,
            total=2,
            accepted=2,
            failed=0,
        )


def test_control_plane_start_work_dispatch_fans_out_start_to_all_workers() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _HandoffDispatch()
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="execution.features",
                        workers=1,
                        nodes=("compute_time_keys", "compute_features"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(state=state, handoff_dispatch=handoff)

    produced = node(
        ControlPlaneStartWorkEvent(
            source_targets=("source:source",),
        ),
        None,
    )
    assert len(produced) == 1
    assert isinstance(produced[0], LogMessage)
    assert produced[0].fields.get("event") == "control_plane.runtime.start_work_broadcast_dispatched"
    assert len(handoff.calls) == 1
    dispatch_call = handoff.calls[0]
    assert dispatch_call["target_group"] == "execution.ingress"
    envelope = dispatch_call["envelope"]
    assert getattr(envelope, "target", None) == "system.cp.leaf_start_work"
    assert isinstance(getattr(envelope, "payload", None), ControlPlaneLeafStartWorkEvent)
    assert getattr(envelope, "payload").source_targets == ("source:source",)


def test_control_plane_start_work_dispatch_is_idempotent() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    handoff = _HandoffDispatch()
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(state=state, handoff_dispatch=handoff)

    first = node(ControlPlaneStartWorkEvent(), None)
    second = node(ControlPlaneStartWorkEvent(), None)

    assert len(first) == 1
    assert isinstance(first[0], LogMessage)
    assert second == []
    assert len(handoff.calls) == 1


def test_control_plane_start_work_dispatch_prefers_handoff_broadcast_over_boundary_service() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    boundary = _BoundaryExec()
    handoff = _HandoffDispatch()
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="execution.features",
                        workers=1,
                        nodes=("compute_time_keys", "compute_features"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(
        state=state,
        boundary_execution=boundary,
        handoff_dispatch=handoff,
    )

    produced = node(ControlPlaneStartWorkEvent(source_targets=("source:source",)), None)

    assert len(produced) == 1
    assert isinstance(produced[0], LogMessage)
    assert produced[0].fields.get("event") == "control_plane.runtime.start_work_broadcast_dispatched"
    assert boundary.calls == []
    assert len(handoff.calls) == 1
