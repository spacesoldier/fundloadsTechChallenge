from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    DefaultControlPlaneDagAssemblyService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
)
from stream_kernel.platform.services.runtime.control_plane_launch_plan import (
    ControlPlaneLaunchPlanService,
)


@dataclass(slots=True)
class _LaunchPlanService(ControlPlaneLaunchPlanService):
    plan: ControlPlaneLaunchPlan | None
    seen_runtime: dict[str, object] | None = None

    def build_plan(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        self.seen_runtime = runtime
        return self.plan


def test_dag_assembly_service_delegates_to_launch_plan_service() -> None:
    runtime = {"platform": {"process_groups": []}}
    plan = ControlPlaneLaunchPlan(
        groups=(ControlPlaneGroupSpec(group_name="execution.alpha", workers=1, nodes=("node.a",)),)
    )
    launch = _LaunchPlanService(plan=plan)
    service = DefaultControlPlaneDagAssemblyService(launch_plan_service=launch)

    assembled = service.assemble(runtime=runtime)

    assert assembled == plan
    assert launch.seen_runtime == runtime


def test_dag_assembly_service_returns_none_when_launch_plan_is_unavailable() -> None:
    launch = _LaunchPlanService(plan=None)
    service = DefaultControlPlaneDagAssemblyService(launch_plan_service=launch)

    assembled = service.assemble(runtime={"platform": {"process_groups": []}})

    assert assembled is None
