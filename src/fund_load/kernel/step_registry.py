from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


# Errors are explicit for fast config feedback (Step Registry spec).
class UnknownStepError(KeyError):
    pass


StepFactory = Callable[[dict[str, object], dict[str, object]], Callable[[object, object], list[object]]]


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
