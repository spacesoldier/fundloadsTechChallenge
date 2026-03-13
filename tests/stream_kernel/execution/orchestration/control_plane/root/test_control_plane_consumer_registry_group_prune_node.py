from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneConsumerRegistryGroupPruneNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
)
from stream_kernel.routing.envelope import Envelope


def test_consumer_registry_group_prune_emits_remove_event_from_launch_plan_nodes() -> None:
    node = ControlPlaneConsumerRegistryGroupPruneNode()
    payload = ControlPlaneLaunchPlanEvent(
        plan=ControlPlaneLaunchPlan(
            groups=(
                ControlPlaneGroupSpec(
                    group_name="execution.ingress",
                    workers=1,
                    nodes=("n.a", "n.b"),
                ),
                ControlPlaneGroupSpec(
                    group_name="execution.features",
                    workers=1,
                    nodes=("n.b", "n.c"),
                ),
            )
        )
    )

    produced = node(Envelope(payload=payload, target="system.cp.consumer_registry_group_prune"), None)

    assert len(produced) == 1
    event = produced[0]
    assert isinstance(event, ControlPlaneConsumerRegistryRemoveNodesEvent)
    assert event.node_names == ("n.a", "n.b", "n.c")


def test_consumer_registry_group_prune_ignores_empty_group_nodes() -> None:
    node = ControlPlaneConsumerRegistryGroupPruneNode()
    payload = ControlPlaneLaunchPlanEvent(
        plan=ControlPlaneLaunchPlan(
            groups=(
                ControlPlaneGroupSpec(
                    group_name="execution.ingress",
                    workers=1,
                    nodes=(),
                ),
            )
        )
    )

    produced = node(Envelope(payload=payload, target="system.cp.consumer_registry_group_prune"), None)

    assert produced == []
