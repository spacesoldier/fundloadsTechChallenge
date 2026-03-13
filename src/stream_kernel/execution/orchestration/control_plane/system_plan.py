from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.kernel.scenario import StepSpec


@dataclass(frozen=True, slots=True)
class ControlPlaneSystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


__all__ = ["ControlPlaneSystemPlan"]
