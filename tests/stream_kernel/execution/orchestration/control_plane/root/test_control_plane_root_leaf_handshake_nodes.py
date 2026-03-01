from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootLeafConfigAckNode,
    ControlPlaneRootLeafConfigAssignNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


def test_root_leaf_config_assign_emits_card_from_leaf_hello_and_launch_plan() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=2,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneRootLeafConfigAssignNode(state=state)
    hello = ControlPlaneLeafHelloEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        pid=1234,
        runner_profile="auto",
    )

    produced = node(hello, None)

    assert len(produced) == 1
    card = produced[0]
    assert isinstance(card, ControlPlaneLeafConfigCardEvent)
    assert card.target_group == "execution.alpha"
    assert card.group_name == "execution.alpha"
    assert card.worker_id == "execution.alpha#1"
    assert card.nodes == ("node.a", "node.b")
    assert card.runner_profile == "auto"
    assert card.worker_slot == 0
    assert card.config_id

    stored = state.events()
    assert isinstance(stored[-2], ControlPlaneLeafHelloEvent)
    assert isinstance(stored[-1], ControlPlaneLeafConfigCardEvent)


def test_root_leaf_config_ack_node_appends_ack_to_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    node = ControlPlaneRootLeafConfigAckNode(state=state)
    ack = ControlPlaneLeafConfigAckEvent(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        config_id="cfg-1",
        status="applied",
        resolved_nodes=("node.a",),
    )

    produced = node(ack, None)

    assert produced == []
    assert state.events() == [ack]


@dataclass(slots=True)
class _NoopState:
    items: list[object] = field(default_factory=list)

    def append_event(self, event: object) -> None:
        self.items.append(event)

    def events(self) -> list[object]:
        return list(self.items)

    def latest(self, *, kind: str) -> object | None:
        _ = kind
        return None


def test_root_leaf_config_assign_returns_no_card_when_group_missing_in_plan() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(groups=(ControlPlaneGroupSpec(group_name="execution.beta"),))
        )
    )
    node = ControlPlaneRootLeafConfigAssignNode(state=state)

    produced = node(
        ControlPlaneLeafHelloEvent(target_group="execution.alpha", worker_id="execution.alpha#1"),
        None,
    )

    assert produced == []
    assert isinstance(state.events()[-1], ControlPlaneLeafHelloEvent)
