from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane import ControlPlaneInitPlanNode
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneGroupSpec,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


def test_control_plane_init_plan_emits_init_launch_and_spawn_from_dag_assembled() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneInitPlanNode(state=state)
    runtime = {"platform": {"process_groups": []}}
    assembled = ControlPlaneDagAssembledEvent(
        runtime=runtime,
        plan=ControlPlaneLaunchPlan(
            groups=(
                ControlPlaneGroupSpec(group_name="execution.alpha", workers=2, nodes=("node.a", "node.b")),
                ControlPlaneGroupSpec(group_name="execution.beta", workers=1, nodes=("node.c",)),
            )
        ),
    )

    produced = node(assembled, None)

    assert produced
    assert isinstance(produced[0], ControlPlaneLaunchPlanEvent)
    assert isinstance(produced[1], ControlPlaneInitializationRequestedEvent)
    spawn_events = [event for event in produced if isinstance(event, ControlPlaneSpawnRequestedEvent)]
    assert len(spawn_events) == 2
    assert {event.group_name for event in spawn_events} == {"execution.alpha", "execution.beta"}

    stored = state.events()
    assert stored
    assert isinstance(stored[0], ControlPlaneLaunchPlanEvent)


def test_control_plane_init_plan_ignores_non_assembled_event() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneInitPlanNode(state=state)

    produced = node(ControlPlaneDagAssemblyRequestedEvent(runtime={"platform": {}}), None)

    assert produced == []
    assert state.events() == []


def test_control_plane_init_plan_deduplicates_same_launch_plan() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneInitPlanNode(state=state)
    assembled = ControlPlaneDagAssembledEvent(
        runtime={"platform": {"process_groups": []}},
        plan=ControlPlaneLaunchPlan(
            groups=(
                ControlPlaneGroupSpec(group_name="execution.alpha", workers=1, nodes=("node.a",)),
            )
        ),
    )

    first = node(assembled, None)
    second = node(assembled, None)

    assert first
    assert any(isinstance(event, ControlPlaneSpawnRequestedEvent) for event in first)
    assert second == []
    assert len(state.events()) == 1
