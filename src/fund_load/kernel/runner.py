from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from fund_load.kernel.context import Context, ContextFactory
from fund_load.kernel.scenario import Scenario


class OutputSink(Protocol):
    # Runner output callback for final messages; sink implementation is external.
    def __call__(self, msg: object) -> None:
        raise NotImplementedError("Runner output sink is a callback")


@dataclass(frozen=True, slots=True)
class Runner:
    # Runner executes a Scenario per input message in strict order (Runner Spec).
    scenario: Scenario
    context_factory: ContextFactory
    on_error: Callable[[Context, Exception], None] | None = None

    def run(self, inputs: Iterable[object], *, output_sink: OutputSink) -> None:
        # Depth-first execution per input ensures deterministic state updates.
        for raw in inputs:
            ctx = self.context_factory.new(line_no=getattr(raw, "line_no", None))
            work: list[object] = [raw]
            try:
                for step_spec in self.scenario.steps:
                    next_work: list[object] = []
                    for msg in work:
                        out_iter = step_spec.step(msg, ctx)
                        next_work.extend(list(out_iter))
                    work = next_work
                    if not work:
                        break
            except Exception as exc:  # pragma: no cover - covered by test via on_error
                if self.on_error is not None:
                    self.on_error(ctx, exc)
                    continue
                raise

            for msg in work:
                output_sink(msg)
