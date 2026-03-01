from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.leaf.system_nodes import (
    ControlPlaneLeafApplyConfigNode,
    ControlPlaneLeafBootstrapNode,
)
from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootLeafConfigAssignNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafPulse,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


def test_leaf_bootstrap_emits_leaf_hello_from_pulse_runtime_metadata() -> None:
    node = ControlPlaneLeafBootstrapNode()
    runtime = {
        "__process_group": "execution.alpha",
        "__worker_id": "execution.alpha#1",
        "__runner_profile_requested": "auto",
    }

    produced = node(ControlPlaneLeafPulse(runtime=runtime), None)

    assert len(produced) == 1
    hello = produced[0]
    assert isinstance(hello, ControlPlaneLeafHelloEvent)
    assert hello.target_group == "execution.alpha"
    assert hello.worker_id == "execution.alpha#1"
    assert hello.runner_profile == "auto"
    assert isinstance(hello.pid, int)


def test_leaf_hello_event_validates_required_fields() -> None:
    try:
        ControlPlaneLeafHelloEvent(target_group="", worker_id="w1")
    except ValueError as exc:
        assert "target_group" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected ValueError for empty target_group")


@dataclass(slots=True)
class _Bootstrapper(ControlPlaneBootstrapperService):
    subset_items: list[ControlPlaneDiscoveryItemEvent] = field(default_factory=list)

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = runtime
        return []

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        _ = runtime
        allowed = set(node_names)
        return [
            item
            for item in self.subset_items
            if item.item_kind == "node"
            and isinstance(item.payload.get("name"), str)
            and item.payload["name"] in allowed
        ]


def test_leaf_startup_sequence_order_is_pulse_hello_card_ack() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.alpha",
                        workers=1,
                        nodes=("node.a", "node.b"),
                    ),
                )
            )
        )
    )
    leaf_bootstrap = ControlPlaneLeafBootstrapNode()
    root_assign = ControlPlaneRootLeafConfigAssignNode(state=state)
    leaf_apply = ControlPlaneLeafApplyConfigNode(
        bootstrapper=_Bootstrapper(
            subset_items=[
                ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.a"}),
                ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "node.b"}),
            ]
        ),
        discovery=InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore()),
    )
    pulse = ControlPlaneLeafPulse(
        runtime={
            "__process_group": "execution.alpha",
            "__worker_id": "execution.alpha#1",
            "__runner_profile_requested": "async",
        }
    )

    hello = leaf_bootstrap(pulse, None)[0]
    card = root_assign(hello, None)[0]
    ack = leaf_apply(card, None)[0]

    assert [type(item) for item in (pulse, hello, card, ack)] == [
        ControlPlaneLeafPulse,
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafConfigCardEvent,
        ControlPlaneLeafConfigAckEvent,
    ]
