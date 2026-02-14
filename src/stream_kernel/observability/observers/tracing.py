from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from stream_kernel.execution.observers.observer import (
    ExecutionObserver,
    ObserverFactoryContext,
    observer_factory,
)
from stream_kernel.kernel.context import Context
from stream_kernel.kernel.trace import ErrorInfo, RouteInfo, TraceRecorder, TraceSpan


class TraceSinkLike(Protocol):
    def emit(self, record: object) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class _FanoutTraceSink:
    # Fan-out wrapper over multiple sinks with exporter-failure isolation.
    def __init__(self, sinks: list[TraceSinkLike]) -> None:
        self._sinks = list(sinks)

    def emit(self, record: object) -> None:
        for sink in self._sinks:
            try:
                sink.emit(record)
            except Exception:
                continue

    def flush(self) -> None:
        for sink in self._sinks:
            try:
                sink.flush()
            except Exception:
                continue

    def close(self) -> None:
        for sink in self._sinks:
            try:
                sink.close()
            except Exception:
                continue


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
            route=_route_info_from_ctx(ctx),
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
    sinks = _build_sinks_from_observability_exporters(ctx)
    tracing = ctx.runtime.get("tracing")
    if not isinstance(tracing, dict):
        tracing = {}
    if sinks:
        sink = _FanoutTraceSink(sinks)
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
        step_indices = {name: idx for idx, name in enumerate(ctx.node_order)}
        return TracingObserver(
            recorder=recorder,
            sink=sink,
            run_id=ctx.run_id,
            scenario_id=ctx.scenario_id,
            step_indices=step_indices,
        )

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
    step_indices = {name: idx for idx, name in enumerate(ctx.node_order)}
    return TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id=ctx.run_id,
        scenario_id=ctx.scenario_id,
        step_indices=step_indices,
    )


def _build_sinks_from_observability_exporters(ctx: ObserverFactoryContext) -> list[TraceSinkLike]:
    observability = ctx.runtime.get("observability", {})
    if not isinstance(observability, dict):
        return []
    tracing_cfg = observability.get("tracing", {})
    if not isinstance(tracing_cfg, dict):
        return []
    exporters = tracing_cfg.get("exporters", [])
    if not isinstance(exporters, list):
        return []

    from stream_kernel.observability.adapters.tracing import (
        trace_jsonl,
        trace_opentracing_bridge,
        trace_otel_otlp,
        trace_stdout,
    )

    factories = {
        "jsonl": trace_jsonl,
        "stdout": trace_stdout,
        "otel_otlp": trace_otel_otlp,
        "opentracing_bridge": trace_opentracing_bridge,
    }
    adapter_aliases = {
        "jsonl": "trace_jsonl",
        "stdout": "trace_stdout",
        "otel_otlp": "trace_otel_otlp",
        "opentracing_bridge": "trace_opentracing_bridge",
    }

    sinks: list[TraceSinkLike] = []
    for exporter in exporters:
        if not isinstance(exporter, dict):
            continue
        kind = exporter.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        settings = exporter.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}

        alias = adapter_aliases.get(kind)
        candidate = ctx.adapter_instances.get(alias) if isinstance(alias, str) else None
        if _is_trace_sink_like(candidate):
            sinks.append(candidate)
            continue

        factory = factories.get(kind)
        if factory is None:
            continue
        built = factory(settings)
        if not _is_trace_sink_like(built):
            raise ValueError("Tracing exporter adapter must expose emit(record), flush(), close()")
        sinks.append(built)

    return sinks


def _is_trace_sink_like(candidate: object) -> bool:
    emit = getattr(candidate, "emit", None)
    flush = getattr(candidate, "flush", None)
    close = getattr(candidate, "close", None)
    return callable(emit) and callable(flush) and callable(close)


def _route_info_from_ctx(ctx: dict[str, object]) -> RouteInfo | None:
    process_group = ctx.get("__process_group")
    handoff_from = ctx.get("__handoff_from")
    route_hop = ctx.get("__route_hop")
    parent_span_id = ctx.get("__parent_span_id")
    normalized_group = process_group if isinstance(process_group, str) and process_group else None
    normalized_from = handoff_from if isinstance(handoff_from, str) and handoff_from else None
    normalized_hop = route_hop if isinstance(route_hop, int) and route_hop >= 0 else None
    normalized_parent = parent_span_id if isinstance(parent_span_id, str) and parent_span_id else None
    if normalized_group is None and normalized_from is None and normalized_hop is None and normalized_parent is None:
        return None
    return RouteInfo(
        process_group=normalized_group,
        handoff_from=normalized_from,
        route_hop=normalized_hop,
        parent_span_id=normalized_parent,
    )
