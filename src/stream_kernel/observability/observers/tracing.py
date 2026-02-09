from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from stream_kernel.execution.observer import (
    ExecutionObserver,
    ObserverFactoryContext,
    observer_factory,
)
from stream_kernel.kernel.context import Context
from stream_kernel.kernel.trace import ErrorInfo, TraceRecorder, TraceSpan


class TraceSinkLike(Protocol):
    def emit(self, record: object) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _TraceState:
    ctx: Context
    span: TraceSpan


class TracingObserver(ExecutionObserver):
    # Execution observer that records per-node traces without wrapping user nodes.
    def __init__(
        self,
        *,
        recorder: TraceRecorder,
        sink: TraceSinkLike,
        run_id: str,
        scenario_id: str,
        step_indices: dict[str, int],
    ) -> None:
        self._recorder = recorder
        self._sink = sink
        self._run_id = run_id
        self._scenario_id = scenario_id
        self._step_indices = dict(step_indices)
        self._contexts: dict[str, Context] = {}

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        if not trace_id:
            return None
        trace_ctx = self._contexts.setdefault(
            trace_id,
            Context(
                trace_id=trace_id,
                run_id=self._run_id,
                scenario_id=self._scenario_id,
                received_at=datetime.now(tz=UTC),
            ),
        )
        span = self._recorder.begin(
            ctx=trace_ctx,
            step_name=node_name,
            step_index=self._step_indices.get(node_name, -1),
            work_index=0,
            msg_in=payload,
        )
        return _TraceState(ctx=trace_ctx, span=span)

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> None:
        if not isinstance(state, _TraceState):
            return
        record = self._recorder.finish(
            ctx=state.ctx,
            span=state.span,
            msg_out=outputs,
            status="ok",
            error=None,
        )
        self._sink.emit(record)

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        if not isinstance(state, _TraceState):
            return
        record = self._recorder.finish(
            ctx=state.ctx,
            span=state.span,
            msg_out=[],
            status="error",
            error=ErrorInfo(
                type=type(error).__name__,
                message=str(error),
                where=node_name,
                stack=None,
            ),
        )
        self._sink.emit(record)

    def on_run_end(self) -> None:
        # Sink lifecycle is finalized once the run loop is drained.
        self._sink.flush()
        self._sink.close()


@observer_factory(name="tracing")
def build_tracing_observer(ctx: ObserverFactoryContext) -> ExecutionObserver | None:
    # Build tracing observer from runtime config and discovered adapter instances.
    tracing = ctx.runtime.get("tracing")
    if not isinstance(tracing, dict) or not tracing.get("enabled"):
        return None

    sink_cfg = tracing.get("sink")
    if not isinstance(sink_cfg, dict):
        return None
    sink_name = sink_cfg.get("name")
    if not isinstance(sink_name, str) or not sink_name:
        return None

    sink = ctx.adapter_instances.get(sink_name)
    if sink is None:
        return None
    if not _is_trace_sink_like(sink):
        raise ValueError("Tracing sink adapter must expose emit(record), flush(), close()")

    signature = tracing.get("signature", {})
    context_diff = tracing.get("context_diff", {})
    if not isinstance(signature, dict):
        signature = {}
    if not isinstance(context_diff, dict):
        context_diff = {}

    recorder = TraceRecorder(
        signature_mode=str(signature.get("mode", "type_only")),
        context_diff_mode=str(context_diff.get("mode", "none")),
        context_diff_whitelist=list(context_diff.get("whitelist", []))
        if isinstance(context_diff.get("whitelist", []), list)
        else None,
    )
    step_indices = {spec.name: idx for idx, spec in enumerate(ctx.step_specs)}
    return TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id=ctx.run_id,
        scenario_id=ctx.scenario_id,
        step_indices=step_indices,
    )


def _is_trace_sink_like(candidate: object) -> bool:
    emit = getattr(candidate, "emit", None)
    flush = getattr(candidate, "flush", None)
    close = getattr(candidate, "close", None)
    return callable(emit) and callable(flush) and callable(close)
