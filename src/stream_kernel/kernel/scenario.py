from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from stream_kernel.kernel.context import Context


# StepSpec binds a named step to a callable (docs/implementation/kernel/Scenario Spec.md).
@dataclass(frozen=True, slots=True)
class StepSpec:
    name: str
    step: Callable[[object, Context | None], Iterable[object]]


@dataclass(frozen=True, slots=True)
class Scenario:
    # Scenario is an immutable ordered list of bound steps.
    scenario_id: str
    steps: Sequence[StepSpec]
