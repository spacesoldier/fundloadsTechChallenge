from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from stream_kernel.kernel.step import Step


# Errors are explicit for fast config feedback (Step Registry spec).
class UnknownStepError(KeyError):
    pass


# Step factories accept dynamic config + wiring and return a Step instance.
# We intentionally use Any here: step input/output types vary per step, and
# the registry sits at a dynamic boundary (docs/implementation/kernel/Step Registry Spec.md).
StepFactory = Callable[[dict[str, object], dict[str, object]], Step[Any, Any]]


@dataclass
class StepRegistry:
    # Registry maps step names to factories (docs/implementation/kernel/Step Registry Spec.md).
    _factories: dict[str, StepFactory] = field(default_factory=dict)

    def register(self, name: str, factory: StepFactory) -> None:
        # Registration is explicit; later registration overrides are allowed by default.
        self._factories[name] = factory

    def get(self, name: str) -> StepFactory:
        if name not in self._factories:
            raise UnknownStepError(name)
        return self._factories[name]
