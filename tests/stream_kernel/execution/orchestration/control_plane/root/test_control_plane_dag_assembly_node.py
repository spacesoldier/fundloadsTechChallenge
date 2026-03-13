from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane import ControlPlaneDagAssemblyNode
from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    ControlPlaneDagAssemblyService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
)


@dataclass(slots=True)
class _Assembly(ControlPlaneDagAssemblyService):
    plan: ControlPlaneLaunchPlan | None

    def assemble(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        _ = runtime
        return self.plan


def test_control_plane_dag_assembly_node_emits_assembled_event() -> None:
    runtime = {"platform": {"process_groups": []}}
    plan = ControlPlaneLaunchPlan(
        groups=(ControlPlaneGroupSpec(group_name="execution.alpha", workers=1, nodes=("node.a",)),)
    )
    node = ControlPlaneDagAssemblyNode(assembly=_Assembly(plan=plan))

    produced = node(ControlPlaneDagAssemblyRequestedEvent(runtime=runtime), None)

    assert produced == [ControlPlaneDagAssembledEvent(runtime=runtime, plan=plan)]


def test_control_plane_dag_assembly_node_drops_empty_plan() -> None:
    node = ControlPlaneDagAssemblyNode(assembly=_Assembly(plan=None))

    produced = node(ControlPlaneDagAssemblyRequestedEvent(runtime={"platform": {}}), None)

    assert produced == []


def test_control_plane_dag_assembly_node_does_not_fallback_to_runtime_groups() -> None:
    node = ControlPlaneDagAssemblyNode(assembly=_Assembly(plan=None))
    runtime = {
        "platform": {
            "process_groups": [
                {
                    "name": "execution.alpha",
                    "workers": 1,
                    "nodes": ["node.a"],
                }
            ]
        }
    }

    produced = node(ControlPlaneDagAssemblyRequestedEvent(runtime=runtime), None)

    assert produced == []
