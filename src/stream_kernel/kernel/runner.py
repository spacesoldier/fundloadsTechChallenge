from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from fund_load.ports.trace_sink import TraceSink

# Kernel runtime types (see docs/implementation/kernel/Runner (Orchestrator) Spec.md).
from stream_kernel.kernel.context import Context, ContextFactory
from stream_kernel.kernel.scenario import Scenario
from stream_kernel.kernel.trace import ErrorInfo, TraceRecorder


class OutputSink(Protocol):
    # Runner output callback for final messages; sink implementation is external.
    # This is a callable contract, not a concrete class (Runner Spec §1).
    def __call__(self, msg: object) -> None:
        # Protocol methods have no implementation; fail fast if called directly.
        raise NotImplementedError("Runner output sink is a callback")


@dataclass(frozen=True, slots=True)
class Runner:
    # Runner executes a Scenario per input message in strict order (Runner Spec).
    scenario: Scenario
    context_factory: ContextFactory
    on_error: Callable[[Context, Exception], None] | None = None
    trace_recorder: TraceRecorder | None = None
    trace_sink: TraceSink | None = None

    def run(self, inputs: Iterable[object], *, output_sink: OutputSink) -> None:
        # Depth-first execution per input ensures deterministic state updates.
        # We process each input to completion before moving to the next one.
        for raw in inputs:
            # Create a fresh Context for this input event (Context Spec).
            ctx = self.context_factory.new(line_no=getattr(raw, "line_no", None))
            # Worklist starts with the raw input message (Step Contract Spec).
            work: list[object] = [raw]
            try:
                # Run the scenario left-to-right (Scenario Spec).
                for step_index, step_spec in enumerate(self.scenario.steps):
                    # Collect outputs from this step for all current work items.
                    next_work: list[object] = []
                    for work_index, msg in enumerate(work):
                        # Each (step, message) pair can have its own trace span.
                        span = None
                        if self.trace_recorder is not None:
                            # Begin trace span before invoking the step (Trace Spec).
                            span = self.trace_recorder.begin(
                                ctx=ctx,
                                step_name=step_spec.name,
                                step_index=step_index,
                                work_index=work_index,
                                msg_in=msg,
                            )
                        try:
                            # Execute the step: may drop/map/fan-out (Step Contract Spec).
                            out_iter = step_spec.step(msg, ctx)
                            # Materialize outputs for determinism and tracing.
                            out_list = list(out_iter)
                        except Exception as exc:  # noqa: BLE001 - trace + rethrow for runner policy
                            # Step raised: record error trace (if enabled).
                            if self.trace_recorder is not None and span is not None:
                                record = self.trace_recorder.finish(
                                    ctx=ctx,
                                    span=span,
                                    msg_out=[],
                                    status="error",
                                    error=ErrorInfo(
                                        type=type(exc).__name__,
                                        message=str(exc),
                                        where=step_spec.name,
                                        stack=None,
                                    ),
                                )
                                # Emit trace record to sink if configured (Trace Spec).
                                if self.trace_sink is not None:
                                    self.trace_sink.emit(record)
                            # Re-raise so runner-level policy can decide (Runner Spec §2.3).
                            raise
                        if self.trace_recorder is not None and span is not None:
                            # Successful step: finalize trace span with outputs.
                            record = self.trace_recorder.finish(
                                ctx=ctx,
                                span=span,
                                msg_out=out_list,
                                status="ok",
                                error=None,
                            )
                            # Emit trace record to sink if configured (Trace Spec).
                            if self.trace_sink is not None:
                                self.trace_sink.emit(record)
                        # Append step outputs to next worklist (fan-out supported).
                        next_work.extend(out_list)
                    # Advance pipeline to next step with outputs from this step.
                    work = next_work
                    # If the step dropped everything, stop early for this input.
                    if not work:
                        break
            except Exception as exc:  # pragma: no cover - covered by test via on_error
                # Runner-level error policy: delegate if handler is provided.
                if self.on_error is not None:
                    self.on_error(ctx, exc)
                    continue
                # Otherwise propagate the error to the caller.
                raise

            # Final outputs for this input are sent to the output sink.
            for msg in work:
                output_sink(msg)

        if self.trace_sink is not None:
            # Best-effort flush/close at end of run (Trace spec §6.1).
            self.trace_sink.flush()
            self.trace_sink.close()
