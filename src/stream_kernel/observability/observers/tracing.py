from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import inspect
from typing import Protocol

from stream_kernel.execution.observers.observer import (
    ExecutionObserver,
    ObserverFactoryContext,
    observer_factory,
)
from stream_kernel.kernel.context import Context
from stream_kernel.kernel.trace import ErrorInfo, RouteInfo, TraceRecorder, TraceSpan
from stream_kernel.observability.events import TraceDispatchEvent
from stream_kernel.routing.envelope import Envelope


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

    async def emit_async(self, record: object) -> None:
        for sink in self._sinks:
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(record)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    continue
                continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(record)
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
    previous_trace_exit: datetime | None = None


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
        excluded_node_names: frozenset[str] = frozenset(),
        trace_queue: object | None = None,
        trace_sink_node_name: str = "system.obs.trace_sink",
        emit_via_runner: bool = False,
    ) -> None:
        self._recorder = recorder
        self._sink = sink
        self._run_id = run_id
        self._scenario_id = scenario_id
        self._step_indices = dict(step_indices)
        self._excluded_node_names = excluded_node_names
        self._trace_queue = trace_queue
        self._trace_sink_node_name = trace_sink_node_name
        self._emit_via_runner = emit_via_runner
        self._contexts: dict[str, Context] = {}

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        if node_name in self._excluded_node_names or node_name.startswith("system.obs."):
            return None
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
        previous_trace_exit: datetime | None = None
        if trace_ctx.trace:
            last_record = trace_ctx.trace[-1]
            last_exit = getattr(last_record, "t_exit", None)
            if isinstance(last_exit, datetime):
                previous_trace_exit = last_exit
        span = self._recorder.begin(
            ctx=trace_ctx,
            step_name=node_name,
            step_index=self._step_indices.get(node_name, -1),
            work_index=0,
            msg_in=payload,
            route=_route_info_from_ctx(ctx),
        )
        return _TraceState(
            ctx=trace_ctx,
            span=span,
            previous_trace_exit=previous_trace_exit,
        )

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> object | None:
        if not isinstance(state, _TraceState):
            return None
        record = self._recorder.finish(
            ctx=state.ctx,
            span=state.span,
            msg_out=outputs,
            status="ok",
            error=None,
        )
        record = _record_with_prev_trace_gap(
            record=record,
            previous_trace_exit=state.previous_trace_exit,
        )
        if state.ctx.trace:
            state.ctx.trace[-1] = record
        if self._emit_via_runner:
            return TraceDispatchEvent(payload=record, trace_id=trace_id)
        self._emit_record(record)
        return None

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> object | None:
        if not isinstance(state, _TraceState):
            return None
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
        record = _record_with_prev_trace_gap(
            record=record,
            previous_trace_exit=state.previous_trace_exit,
        )
        if state.ctx.trace:
            state.ctx.trace[-1] = record
        if self._emit_via_runner:
            return TraceDispatchEvent(payload=record, trace_id=trace_id)
        self._emit_record(record)
        return None

    def on_run_end(self) -> None:
        if self._trace_queue is not None:
            # Sink lifecycle is owned by TraceSinkNode; runner drain handles flush/close.
            return
        self._sink.flush()
        self._sink.close()

    def on_trace_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        _ = (trace_id, attributes)
        self._emit_record(event)

    async def on_trace_event_async(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        _ = (trace_id, attributes)
        await self._emit_record_async(event)

    def _emit_record(self, record: object) -> None:
        # Route the record through the work queue when available; fall back to direct emit.
        if self._trace_queue is not None:
            push = getattr(self._trace_queue, "push", None)
            if callable(push):
                push(Envelope(payload=record, target=self._trace_sink_node_name))
                return
        self._sink.emit(record)

    async def _emit_record_async(self, record: object) -> None:
        if self._trace_queue is not None:
            push = getattr(self._trace_queue, "push", None)
            if callable(push):
                push(Envelope(payload=record, target=self._trace_sink_node_name))
                return
        emit_async = getattr(self._sink, "emit_async", None)
        if callable(emit_async):
            await emit_async(record)
            return
        self._sink.emit(record)


@observer_factory(name="tracing")
def build_tracing_observer(ctx: ObserverFactoryContext) -> ExecutionObserver | None:
    # Build tracing observer from runtime config and discovered adapter instances.
    sinks = _build_sinks_from_observability_exporters(ctx)
    emit_via_runner = _is_trace_dispatch_enabled(ctx.runtime)
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
            emit_via_runner=emit_via_runner,
        )

    # Supervisor-owned tracing in worker runtimes: no local sinks, but records must still be
    # produced and dispatched back through runner/system-node rails.
    if _is_worker_process_role(runtime=ctx.runtime) and emit_via_runner:
        recorder = TraceRecorder(
            signature_mode="type_only",
            context_diff_mode="none",
            context_diff_whitelist=(),
        )
        step_indices = {name: idx for idx, name in enumerate(ctx.node_order)}
        return TracingObserver(
            recorder=recorder,
            sink=_FanoutTraceSink([]),
            run_id=ctx.run_id,
            scenario_id=ctx.scenario_id,
            step_indices=step_indices,
            emit_via_runner=True,
        )

    # Dedicated service-process mode: supervisor remains transport-only and dispatches
    # trace records via runner/system-node rails without local sink ownership.
    if _is_supervisor_transport_only_role(runtime=ctx.runtime) and emit_via_runner:
        recorder = TraceRecorder(
            signature_mode="type_only",
            context_diff_mode="none",
            context_diff_whitelist=(),
        )
        step_indices = {name: idx for idx, name in enumerate(ctx.node_order)}
        return TracingObserver(
            recorder=recorder,
            sink=_FanoutTraceSink([]),
            run_id=ctx.run_id,
            scenario_id=ctx.scenario_id,
            step_indices=step_indices,
            emit_via_runner=True,
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
        emit_via_runner=_is_trace_dispatch_enabled(ctx.runtime),
    )


def _build_sinks_from_observability_exporters(ctx: ObserverFactoryContext) -> list[TraceSinkLike]:
    observability = ctx.runtime.get("observability", {})
    if not isinstance(observability, dict):
        return []
    tracing_cfg = observability.get("tracing", {})
    if not isinstance(tracing_cfg, dict):
        return []
    if _is_worker_process_role(runtime=ctx.runtime):
        return []
    exporters = tracing_cfg.get("exporters", [])
    if not isinstance(exporters, list):
        return []
    strict = bool(ctx.runtime.get("strict", True))

    adapter_aliases = {
        "jsonl": "trace_jsonl",
        "stdout": "trace_stdout",
        "otel_otlp": "trace_otel_otlp",
        "otel_otlp_logical": "trace_otel_otlp",
        "otel_otlp_topology": "trace_otel_otlp",
        "opentracing_bridge": "trace_opentracing_bridge",
    }

    sinks: list[TraceSinkLike] = []
    for index, exporter in enumerate(exporters):
        if not isinstance(exporter, dict):
            continue
        if exporter.get("enabled") is False:
            continue
        kind = exporter.get("kind")
        if not isinstance(kind, str) or not kind:
            continue

        alias = adapter_aliases.get(kind)
        if not isinstance(alias, str):
            if strict:
                raise ValueError(
                    f"runtime.observability.tracing.exporters[{index}] kind '{kind}' is not supported"
                )
            continue
        candidate = ctx.adapter_instances.get(f"{alias}#{index}")
        if candidate is None:
            candidate = ctx.adapter_instances.get(alias)
        if _is_trace_sink_like(candidate):
            sinks.append(candidate)
            continue

        if candidate is None:
            if strict and not _is_supervisor_transport_only_role(runtime=ctx.runtime):
                raise ValueError(
                    "runtime.observability.tracing."
                    f"exporters[{index}] sink binding is missing for adapter '{alias}'"
                )
            continue

        raise ValueError("Tracing exporter adapter must expose emit(record), flush(), close()")

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
    runner_gap_ms = ctx.get("__runner_gap_ms")
    normalized_group = process_group if isinstance(process_group, str) and process_group else None
    normalized_from = handoff_from if isinstance(handoff_from, str) and handoff_from else None
    normalized_hop = route_hop if isinstance(route_hop, int) and route_hop >= 0 else None
    normalized_parent = parent_span_id if isinstance(parent_span_id, str) and parent_span_id else None
    normalized_gap = float(runner_gap_ms) if isinstance(runner_gap_ms, (int, float)) and runner_gap_ms >= 0 else None
    if (
        normalized_group is None
        and normalized_from is None
        and normalized_hop is None
        and normalized_parent is None
        and normalized_gap is None
    ):
        return None
    return RouteInfo(
        process_group=normalized_group,
        handoff_from=normalized_from,
        route_hop=normalized_hop,
        parent_span_id=normalized_parent,
        runner_gap_ms=normalized_gap,
    )


def _record_with_prev_trace_gap(
    *,
    record: object,
    previous_trace_exit: datetime | None,
) -> object:
    route = getattr(record, "route", None)
    t_enter = getattr(record, "t_enter", None)
    if not isinstance(t_enter, datetime):
        return record
    fallback_gap = route.runner_gap_ms if isinstance(route, RouteInfo) else None
    effective_gap = _resolve_prev_trace_gap_ms(
        previous_trace_exit=previous_trace_exit,
        current_enter=t_enter,
        fallback_gap_ms=fallback_gap,
    )
    if effective_gap is None:
        return record
    if isinstance(route, RouteInfo):
        patched_route = RouteInfo(
            process_group=route.process_group,
            handoff_from=route.handoff_from,
            route_hop=route.route_hop,
            parent_span_id=route.parent_span_id,
            runner_gap_ms=effective_gap,
        )
    else:
        patched_route = RouteInfo(runner_gap_ms=effective_gap)
    try:
        return replace(record, route=patched_route)
    except Exception:
        return record


def _resolve_prev_trace_gap_ms(
    *,
    previous_trace_exit: datetime | None,
    current_enter: datetime,
    fallback_gap_ms: float | None,
) -> float | None:
    if isinstance(previous_trace_exit, datetime):
        delta_ms = (current_enter - previous_trace_exit).total_seconds() * 1000.0
        if delta_ms >= 0:
            return delta_ms
    return fallback_gap_ms


def _is_trace_dispatch_enabled(runtime: dict[str, object]) -> bool:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    if _is_worker_process_role(runtime=runtime):
        return _has_enabled_tracing_exporters(runtime=runtime)
    tracing_cfg = observability.get("tracing", {})
    pipeline = observability.get("pipeline")
    if isinstance(pipeline, dict) and "system_nodes" in pipeline:
        system_nodes = pipeline.get("system_nodes", [])
        if not isinstance(system_nodes, list):
            return False
        for cfg in system_nodes:
            if not isinstance(cfg, dict):
                continue
            kind = cfg.get("kind")
            enabled = cfg.get("enabled", True)
            if kind == "system.obs.trace_dispatch" and enabled is not False:
                return True
        return False
    if not isinstance(tracing_cfg, dict):
        return False
    exporters = tracing_cfg.get("exporters", [])
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(exporter, dict) and exporter.get("enabled", True) is not False
        for exporter in exporters
    )


def _has_enabled_tracing_exporters(*, runtime: dict[str, object]) -> bool:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    tracing_cfg = observability.get("tracing", {})
    if not isinstance(tracing_cfg, dict):
        return False
    exporters = tracing_cfg.get("exporters", [])
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(exporter, dict) and exporter.get("enabled", True) is not False
        for exporter in exporters
    )


def _is_worker_process_role(*, runtime: dict[str, object]) -> bool:
    role = runtime.get("__process_role")
    return isinstance(role, str) and role == "worker"


def _is_supervisor_transport_only_role(*, runtime: dict[str, object]) -> bool:
    role = runtime.get("__process_role")
    if isinstance(role, str) and role in {"worker", "observability_worker"}:
        return False

    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return False
    bootstrap = platform.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        return False
    if bootstrap.get("mode") != "process_supervisor":
        return False

    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    service_process = observability.get("service_process", {})
    if not isinstance(service_process, dict):
        return False
    return service_process.get("enabled") is True
