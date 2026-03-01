from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLaunchPlan,
)
from stream_kernel.platform.services.runtime.control_plane_launch_plan import (
    ControlPlaneLaunchPlanService,
)


@runtime_checkable
class ControlPlaneDagAssemblyService(Protocol):
    def assemble(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        raise NotImplementedError("ControlPlaneDagAssemblyService.assemble must be implemented")


@service(name="control_plane_dag_assembly_service")
@dataclass(slots=True)
class DefaultControlPlaneDagAssemblyService(ControlPlaneDagAssemblyService):
    launch_plan_service: ControlPlaneLaunchPlanService = inject.service(ControlPlaneLaunchPlanService)

    def assemble(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        return self.launch_plan_service.build_plan(runtime=runtime)


__all__ = [
    "ControlPlaneDagAssemblyService",
    "DefaultControlPlaneDagAssemblyService",
]
