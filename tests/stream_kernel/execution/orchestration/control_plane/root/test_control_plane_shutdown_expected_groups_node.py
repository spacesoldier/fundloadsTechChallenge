from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneShutdownExpectedGroupsNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _Readiness:
    configured_groups: tuple[str, ...] | None = None

    def configure_expected_groups(self, groups: tuple[str, ...]) -> None:
        self.configured_groups = tuple(groups)


def test_shutdown_expected_groups_node_configures_expected_groups_from_launch_plan() -> None:
    readiness = _Readiness()
    node = ControlPlaneShutdownExpectedGroupsNode(shutdown_readiness=readiness)  # type: ignore[arg-type]
    payload = ControlPlaneLaunchPlanEvent(
        plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("n1",)),
                    ControlPlaneGroupSpec(group_name="execution.features", workers=2, nodes=("n2",)),
                    ControlPlaneGroupSpec(
                        group_name="system.observability",
                        workers=1,
                        nodes=("system.obs.trace_dispatch",),
                    ),
                    ControlPlaneGroupSpec(group_name="execution.ingress", workers=1, nodes=("n3",)),
                )
            )
        )

    produced = node(Envelope(payload=payload, target="system.cp.shutdown_expected_groups"), None)

    assert produced == []
    assert readiness.configured_groups == ("execution.features", "execution.ingress")
