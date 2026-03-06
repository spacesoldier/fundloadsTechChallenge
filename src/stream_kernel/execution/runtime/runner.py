from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field, replace
from threading import Event
from typing import Callable

from stream_kernel.application_context.inject import inject
from stream_kernel.integration.work_queue import QueuePort
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.observability import (
    ObservabilityPipelineService,
    ObservabilityService,
    resolve_pipeline_observability,
)
from stream_kernel.observability.events import (
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    TraceDispatchEvent,
)
from stream_kernel.platform.services.state.context import ContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult
from stream_kernel.routing.routing_service import RoutingService

_ORDERED_SINK_MODES = {"completion", "source_seq"}
_SYNC_IDLE_WAIT = Event()


@dataclass(slots=True)
class SyncRunner:
    # Synchronous execution engine.
    # Responsibilities:
    # - pull work items from QueuePort;
    # - resolve context metadata by trace_id via ContextService;
    # - invoke target node;
    # - route node outputs via RoutingService;
    # - push downstream envelopes back to QueuePort.
    #
    # This runner does not own dependency lifecycle: services/ports are injected by framework DI.
    nodes: dict[str, object]
    # Queue/routing are framework-managed dependencies and must come from DI.
    work_queue: object = inject.queue(Envelope, qualifier="execution.cpu")
    router: object = inject.service(RoutingService)
    # Resolved through DI (`inject.service(ContextService)` in ApplicationContext wiring phase).
    context_service: object = inject.service(ContextService)
    # Framework-level observability gateway (tracing/metrics/logging hooks).
    observability: object = inject.service(ObservabilityService)
    # Service/system nodes can request full metadata, regular nodes receive filtered view.
    full_context_nodes: set[str] = field(default_factory=set)
    # Sink delivery ordering mode: `completion` (default) or `source_seq`.
    ordered_sink_mode: str = "completion"
    # Boundary mode: collect cross-group / terminal envelopes instead of failing on unknown local targets.
    allow_external_deliveries: bool = False
    # Optional sink for envelopes targeted outside this runner node set.
    external_deliveries: list[Envelope] | None = None
    # Optional sink for terminal envelopes collected during run().
    terminal_outputs: list[Envelope] | None = None
    # Optional sink preserving boundary output order (terminal + external deliveries).
    boundary_outputs: list[Envelope] | None = None
    # Optional hook to enrich per-envelope observability context.
    observability_context_enricher: Callable[[Envelope, dict[str, object]], dict[str, object] | None] | None = None
    # Graceful stop: when True, runner empties the queue before honouring the stop request.
    drain_on_stop: bool = True
    _stop_requested: bool = field(default=False, init=False)
    _last_seen_by_trace: dict[str, float] = field(default_factory=dict, init=False)

    def request_stop(self) -> None:
        # Graceful stop signal: runner finishes inflight queue when drain_on_stop=True.
        self._stop_requested = True

    def run(self) -> None:
        # Drain current queue until empty.
        # Determinism: each popped envelope is fully executed and routed before next pop.
        context_service = self._context_service()
        work_queue = self._work_queue()
        router = self._router()
        observability = self._observability()
        while True:
            if self._stop_requested:
                size_fn = getattr(work_queue, "size", None)
                size = size_fn() if callable(size_fn) else 0
                if not self.drain_on_stop or size == 0:
                    break
            item = work_queue.pop()
            if item is None:
                break
            envelope = self._normalize(item)
            # Target must already be resolved by router. Runner executes, not decides topology.
            if envelope.target is None:
                raise ValueError("Envelope.target must be set before execution")
            target = envelope.target
            if isinstance(target, str):
                node_name = target
            else:
                raise ValueError("Envelope.target must resolve to a single node")
            if node_name not in self.nodes:
                if self.allow_external_deliveries:
                    self._collect_external_delivery(envelope)
                    continue
                raise ValueError(f"Unknown node '{node_name}'")

            is_sink_node = node_name.startswith("sink:")
            full_ctx = context_service.metadata(envelope.trace_id, full=True)
            if self.ordered_sink_mode == "source_seq" and is_sink_node:
                if not isinstance(full_ctx.get("__seq"), int):
                    raise ValueError(
                        f"Missing __seq in context for sink node '{node_name}' "
                        "while runtime.ordering.sink_mode=source_seq"
                    )
            # Context is loaded by trace_id. `full` grants internal keys for service/system nodes.
            raw_ctx = full_ctx if (node_name in self.full_context_nodes) else {
                key: value for key, value in full_ctx.items() if not key.startswith("__")
            }
            observability_ctx = dict(raw_ctx)
            if isinstance(envelope.span_id, str) and envelope.span_id:
                observability_ctx["__parent_span_id"] = envelope.span_id
            self._stamp_runner_gap(
                envelope=envelope,
                observability_ctx=observability_ctx,
            )
            self._enrich_observability_ctx(envelope, observability_ctx)
            # Pass a copy to the node so it cannot mutate persisted context in-place by accident.
            node_ctx = dict(raw_ctx)
            node = self.nodes[node_name]
            observability_enabled = not self._is_observability_system_node(node_name)
            # Observability hooks can keep per-node temporary state (timers, snapshots, counters).
            observer_state = (
                observability.before_node(
                    node_name=node_name,
                    payload=envelope.payload,
                    ctx=observability_ctx,
                    trace_id=envelope.trace_id,
                )
                if observability_enabled
                else None
            )
            try:
                # Node contract is `(payload, ctx) -> iterable[output]`.
                outputs = list(node(envelope.payload, node_ctx))
            except Exception as exc:
                # Error path is explicitly observable for diagnostics and metrics.
                if observability_enabled:
                    error_service_outputs = observability.on_node_error(
                        node_name=node_name,
                        payload=envelope.payload,
                        ctx=observability_ctx,
                        trace_id=envelope.trace_id,
                        error=exc,
                        state=observer_state,
                    )
                self._route_observability_service_outputs(
                    service_outputs=error_service_outputs,
                    source_node=node_name,
                    trace_id=envelope.trace_id,
                    reply_to=envelope.reply_to,
                    span_id=self._span_id_from_observer_state(observer_state),
                    tombstone=envelope.tombstone,
                    work_queue=work_queue,
                    router=router,
                )
                raise
            # Success path callback after node output materialization.
            produced_span_id = self._span_id_from_observer_state(observer_state)
            if observability_enabled:
                after_service_outputs = observability.after_node(
                    node_name=node_name,
                    payload=envelope.payload,
                    ctx=observability_ctx,
                    trace_id=envelope.trace_id,
                    outputs=outputs,
                    state=observer_state,
                )
                self._route_observability_service_outputs(
                    service_outputs=after_service_outputs,
                    source_node=node_name,
                    trace_id=envelope.trace_id,
                    reply_to=envelope.reply_to,
                    span_id=produced_span_id,
                    tombstone=envelope.tombstone,
                    work_queue=work_queue,
                    router=router,
                )

            # Router translates outputs to concrete `(target_node, payload)` deliveries.
            # Envelope trace_id emitted by node output overrides current trace_id if present.
            for output in outputs:
                terminal = self._terminal_event_from_output(output)
                if terminal is not None:
                    terminal_trace_id = output.trace_id if isinstance(output, Envelope) else None
                    resolved_trace_id = terminal_trace_id or envelope.trace_id
                    terminal_service_outputs = self._emit_terminal_event(
                        trace_id=resolved_trace_id,
                        terminal=terminal,
                    )
                    self._collect_terminal_output(
                        Envelope(
                            payload=terminal,
                            trace_id=resolved_trace_id,
                            reply_to=envelope.reply_to,
                            span_id=produced_span_id,
                        )
                    )
                    self._route_observability_service_outputs(
                        service_outputs=terminal_service_outputs,
                        source_node=node_name,
                        trace_id=resolved_trace_id,
                        reply_to=envelope.reply_to,
                        span_id=produced_span_id,
                        tombstone=bool(envelope.tombstone),
                        work_queue=work_queue,
                        router=router,
                    )
                    continue

                explicit_trace_id = output.trace_id if isinstance(output, Envelope) else None
                explicit_reply_to = output.reply_to if isinstance(output, Envelope) else None
                explicit_span_id = output.span_id if isinstance(output, Envelope) else None
                explicit_tombstone = output.tombstone if isinstance(output, Envelope) else False
                if self.allow_external_deliveries and isinstance(output, Envelope) and output.target is not None:
                    target_names = (
                        [output.target]
                        if isinstance(output.target, str)
                        else list(output.target)
                    )
                    downstream_tombstone = bool(explicit_tombstone or envelope.tombstone)
                    for target_name in target_names:
                        envelope_out = Envelope(
                            payload=output.payload,
                            target=target_name,
                            trace_id=explicit_trace_id or envelope.trace_id,
                            reply_to=explicit_reply_to or envelope.reply_to,
                            span_id=explicit_span_id or produced_span_id,
                            tombstone=downstream_tombstone,
                        )
                        if self.allow_external_deliveries and target_name not in self.nodes:
                            self._collect_external_delivery(envelope_out)
                            continue
                        work_queue.push(envelope_out)
                    continue
                try:
                    routing_result = router.route([output], source=node_name)
                except ValueError as exc:
                    if self.allow_external_deliveries and "No consumers registered" in str(exc):
                        self._collect_terminal_output(
                            Envelope(
                                payload=output.payload if isinstance(output, Envelope) else output,
                                trace_id=explicit_trace_id or envelope.trace_id,
                                reply_to=explicit_reply_to or envelope.reply_to,
                                span_id=explicit_span_id or produced_span_id,
                                tombstone=bool(explicit_tombstone or envelope.tombstone),
                            )
                        )
                        continue
                    raise
                downstream_trace_id = explicit_trace_id or envelope.trace_id
                downstream_reply_to = explicit_reply_to or envelope.reply_to
                downstream_span_id = explicit_span_id or produced_span_id
                downstream_tombstone = bool(explicit_tombstone or envelope.tombstone)
                for target_name, payload in self._local_deliveries(routing_result):
                    envelope_out = Envelope(
                        payload=payload,
                        target=target_name,
                        trace_id=downstream_trace_id,
                        reply_to=downstream_reply_to,
                        span_id=downstream_span_id,
                        tombstone=downstream_tombstone,
                    )
                    if self.allow_external_deliveries and target_name not in self.nodes:
                        self._collect_external_delivery(envelope_out)
                        continue
                    work_queue.push(envelope_out)

    def on_run_end(self) -> None:
        # Finalize observability lifecycle once run loop is completed.
        self._observability().on_run_end()

    @staticmethod
    def _normalize(item: object) -> Envelope:
        # Internal queue contract: runner consumes only Envelope items.
        if isinstance(item, Envelope):
            return item
        raise ValueError("QueuePort must contain Envelope instances")

    def _context_service(self) -> ContextService:
        # Runtime guard: DI must resolve `inject.service(ContextService)` before execution starts.
        if not isinstance(self.context_service, ContextService):
            raise ValueError("SyncRunner context_service is not resolved via DI")
        return self.context_service

    def _work_queue(self) -> QueuePort:
        # Runtime guard: DI must resolve `inject.queue(Envelope, qualifier="execution.cpu")`.
        if isinstance(self.work_queue, QueuePort):
            return self.work_queue
        if callable(getattr(self.work_queue, "push", None)) and callable(
            getattr(self.work_queue, "pop", None)
        ):
            return self.work_queue  # type: ignore[return-value]
        raise ValueError("SyncRunner work_queue is not resolved via DI")

    def _router(self) -> RoutingService:
        # Runtime guard: DI must resolve `inject.service(RoutingService)`.
        if isinstance(self.router, RoutingService):
            return self.router
        if callable(getattr(self.router, "route", None)):
            return self.router  # type: ignore[return-value]
        raise ValueError("SyncRunner router is not resolved via DI")

    def _observability(self) -> ObservabilityPipelineService:
        # Runtime guard: DI must resolve `inject.service(ObservabilityService)`.
        resolved = resolve_pipeline_observability(self.observability)
        if isinstance(resolved, ObservabilityPipelineService):
            return resolved
        raise ValueError("SyncRunner observability is not resolved via DI")

    @staticmethod
    def _local_deliveries(route_result: object) -> list[tuple[str, object]]:
        # Routing contract is strict: Router/Service must return RoutingResult.
        if not isinstance(route_result, RoutingResult):
            raise ValueError("RoutingService.route must return RoutingResult")
        return route_result.local_deliveries

    @staticmethod
    def _terminal_event_from_output(output: object) -> TerminalEvent | None:
        if isinstance(output, TerminalEvent):
            return output
        if isinstance(output, Envelope) and isinstance(output.payload, TerminalEvent):
            return output.payload
        return None

    @staticmethod
    def _seed_context(
        *,
        context_service: ContextService,
        trace_id: str,
        payload: object,
        run_id: str,
        scenario_id: str,
        reply_to: str | None = None,
    ) -> None:
        # Compatibility bridge: pass reply metadata when service supports it.
        if reply_to is None:
            context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=run_id,
                scenario_id=scenario_id,
            )
            return
        try:
            context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=run_id,
                scenario_id=scenario_id,
                reply_to=reply_to,
            )
        except TypeError:
            context_service.seed(
                trace_id=trace_id,
                payload=payload,
                run_id=run_id,
                scenario_id=scenario_id,
            )

    @staticmethod
    def _trace_id(*, run_id: str, index: int) -> str:
        # Stable trace id format for reproducible runs.
        return f"{run_id}:{index}"

    def _emit_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> object | None:
        return self._observability().on_ingress(trace_id=trace_id, reply_to=reply_to)

    def _emit_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal: TerminalEvent | None,
    ) -> object | None:
        return self._observability().on_terminal_event(trace_id=trace_id, terminal_event=terminal)

    @staticmethod
    def _span_id_from_observer_state(state: object) -> str | None:
        states = state if isinstance(state, list) else [state]
        for item in states:
            span = getattr(item, "span", None)
            span_id = getattr(span, "span_id", None)
            if isinstance(span_id, str) and span_id:
                return span_id
            candidate = getattr(item, "span_id", None)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    @staticmethod
    def _is_observability_system_node(node_name: str) -> bool:
        return (
            node_name.startswith("system.obs.")
            or node_name.startswith("system.transport.handoff.")
        )

    def __post_init__(self) -> None:
        if self.ordered_sink_mode not in _ORDERED_SINK_MODES:
            raise ValueError(
                "SyncRunner ordered_sink_mode must be one of: "
                f"{sorted(_ORDERED_SINK_MODES)}"
            )

    @staticmethod
    def _coerce_observability_service_outputs(candidate: object) -> list[object]:
        if candidate is None:
            return []
        if isinstance(candidate, list):
            return [item for item in candidate if item is not None]
        return [candidate]

    def _route_observability_service_outputs(
        self,
        *,
        service_outputs: object,
        source_node: str,
        trace_id: str | None,
        reply_to: str | None,
        span_id: str | None,
        tombstone: bool = False,
        work_queue: QueuePort,
        router: RoutingService,
    ) -> None:
        for output in self._coerce_observability_service_outputs(service_outputs):
            output = self._normalize_observability_output(
                output=output,
                source_node=source_node,
                trace_id=trace_id,
            )
            explicit_trace_id = output.trace_id if isinstance(output, Envelope) else None
            explicit_reply_to = output.reply_to if isinstance(output, Envelope) else None
            explicit_span_id = output.span_id if isinstance(output, Envelope) else None
            explicit_tombstone = output.tombstone if isinstance(output, Envelope) else False
            if self.allow_external_deliveries and isinstance(output, Envelope) and output.target is not None:
                target_names = (
                    [output.target]
                    if isinstance(output.target, str)
                    else list(output.target)
                )
                downstream_trace_id = explicit_trace_id or trace_id
                downstream_reply_to = explicit_reply_to or reply_to
                downstream_span_id = explicit_span_id or span_id
                downstream_tombstone = bool(explicit_tombstone or tombstone)
                for target_name in target_names:
                    envelope_out = Envelope(
                        payload=output.payload,
                        target=target_name,
                        trace_id=downstream_trace_id,
                        reply_to=downstream_reply_to,
                        span_id=downstream_span_id,
                        tombstone=downstream_tombstone,
                    )
                    if self.allow_external_deliveries and target_name not in self.nodes:
                        self._collect_external_delivery(envelope_out)
                        continue
                    work_queue.push(envelope_out)
                continue
            try:
                routing_result = router.route([output], source=source_node)
            except ValueError as exc:
                if self.allow_external_deliveries and "No consumers registered" in str(exc):
                    self._collect_terminal_output(
                        Envelope(
                            payload=output.payload if isinstance(output, Envelope) else output,
                            trace_id=explicit_trace_id or trace_id,
                            reply_to=explicit_reply_to or reply_to,
                            span_id=explicit_span_id or span_id,
                            tombstone=bool(explicit_tombstone or tombstone),
                        )
                    )
                    continue
                raise
            downstream_trace_id = explicit_trace_id or trace_id
            downstream_reply_to = explicit_reply_to or reply_to
            downstream_span_id = explicit_span_id or span_id
            downstream_tombstone = bool(explicit_tombstone or tombstone)
            for target_name, payload in SyncRunner._local_deliveries(routing_result):
                envelope_out = Envelope(
                    payload=payload,
                    target=target_name,
                    trace_id=downstream_trace_id,
                    reply_to=downstream_reply_to,
                    span_id=downstream_span_id,
                    tombstone=downstream_tombstone,
                )
                if self.allow_external_deliveries and target_name not in self.nodes:
                    self._collect_external_delivery(envelope_out)
                    continue
                work_queue.push(envelope_out)

    @staticmethod
    def _normalize_observability_output(
        *,
        output: object,
        source_node: str,
        trace_id: str | None,
    ) -> object:
        if not isinstance(trace_id, str) or not trace_id:
            return output
        if not isinstance(
            output,
            (TraceDispatchEvent, LogDispatchEvent, MetricDispatchEvent, MonitorDispatchEvent),
        ):
            return output
        attrs = dict(output.attributes)
        attrs.setdefault("source_node", source_node)
        attrs.setdefault("event_payload_type", type(output.payload).__name__)
        attrs.setdefault("correlation_id", trace_id)
        if output.trace_id is None:
            return replace(output, trace_id=trace_id, attributes=attrs)
        if attrs != output.attributes:
            return replace(output, attributes=attrs)
        return output

    def _collect_external_delivery(self, envelope: Envelope) -> None:
        if isinstance(self.external_deliveries, list):
            self.external_deliveries.append(envelope)
        if isinstance(self.boundary_outputs, list):
            self.boundary_outputs.append(envelope)

    def _collect_terminal_output(self, envelope: Envelope) -> None:
        if isinstance(self.terminal_outputs, list):
            self.terminal_outputs.append(envelope)
        if isinstance(self.boundary_outputs, list):
            self.boundary_outputs.append(envelope)

    def _enrich_observability_ctx(self, envelope: Envelope, observability_ctx: dict[str, object]) -> None:
        enrich = self.observability_context_enricher
        if not callable(enrich):
            return
        extra = enrich(envelope, dict(observability_ctx))
        if isinstance(extra, dict):
            observability_ctx.update(extra)

    def _stamp_runner_gap(self, *, envelope: Envelope, observability_ctx: dict[str, object]) -> None:
        trace_id = envelope.trace_id
        if not isinstance(trace_id, str) or not trace_id:
            return
        now = time.monotonic()
        prev = self._last_seen_by_trace.get(trace_id)
        self._last_seen_by_trace[trace_id] = now
        if prev is None:
            return
        observability_ctx["__runner_gap_ms"] = (now - prev) * 1000.0

    def run_until_stopped(
        self,
        *,
        poll_timeout_seconds: float = 0.01,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        # Long-running loop variant: drain work, then block efficiently waiting for new items.
        # Exit on explicit stop (respecting drain_on_stop), queue close, or optional idle timeout.
        work_queue = self._work_queue()
        poll_timeout = max(0.0, float(poll_timeout_seconds))
        idle_deadline = (time.monotonic() + float(idle_timeout_seconds)) if isinstance(idle_timeout_seconds, (int, float)) and float(idle_timeout_seconds) > 0 else None
        while True:
            self.run()
            if self._stop_requested:
                size_fn = getattr(work_queue, "size", None)
                size = size_fn() if callable(size_fn) else 0
                if not self.drain_on_stop or size == 0:
                    return
            if callable(getattr(work_queue, "is_closed", None)) and bool(work_queue.is_closed()):
                if work_queue.size() == 0:
                    return
            now = time.monotonic()
            if idle_deadline is not None and now >= idle_deadline and work_queue.size() == 0:
                return
            wait_fn = getattr(work_queue, "wait_for_item", None)
            if not callable(wait_fn):
                if poll_timeout > 0:
                    _SYNC_IDLE_WAIT.wait(poll_timeout)
                continue
            if wait_fn(poll_timeout):
                idle_deadline = (time.monotonic() + float(idle_timeout_seconds)) if idle_deadline is not None else None
                continue
            if idle_deadline is not None and work_queue.size() == 0 and time.monotonic() >= idle_deadline:
                return


@dataclass(slots=True)
class AsyncRunner:
    # Async execution engine for IO-bound/awaitable nodes.
    # Semantics mirror SyncRunner: deterministic queue drain + routing + observability callbacks.
    nodes: dict[str, object]
    work_queue: object = inject.queue(Envelope, qualifier="execution.asyncio")
    router: object = inject.service(RoutingService)
    context_service: object = inject.service(ContextService)
    observability: object = inject.service(ObservabilityService)
    full_context_nodes: set[str] = field(default_factory=set)
    ordered_sink_mode: str = "completion"
    allow_external_deliveries: bool = False
    external_deliveries: list[Envelope] | None = None
    terminal_outputs: list[Envelope] | None = None
    boundary_outputs: list[Envelope] | None = None
    observability_context_enricher: Callable[[Envelope, dict[str, object]], dict[str, object] | None] | None = None
    drain_on_stop: bool = True
    _stop_requested: bool = field(default=False, init=False)
    _last_seen_by_trace: dict[str, float] = field(default_factory=dict, init=False)

    def run(self) -> None:
        _run_async_blocking(self.run_async())

    def run_until_stopped(
        self,
        *,
        poll_timeout_seconds: float = 0.01,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        _run_async_blocking(
            self.run_until_stopped_async(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
        )

    async def run_async(self) -> None:
        context_service = self._context_service()
        work_queue = self._work_queue()
        router = self._router()
        observability = self._observability()
        while True:
            if self._stop_requested and (not self.drain_on_stop or work_queue.size() == 0):
                break
            item = work_queue.pop()
            if item is None:
                break
            envelope = SyncRunner._normalize(item)
            if envelope.target is None:
                raise ValueError("Envelope.target must be set before execution")
            target = envelope.target
            if isinstance(target, str):
                node_name = target
            else:
                raise ValueError("Envelope.target must resolve to a single node")
            if node_name not in self.nodes:
                if self.allow_external_deliveries:
                    self._collect_external_delivery(envelope)
                    continue
                raise ValueError(f"Unknown node '{node_name}'")

            is_sink_node = node_name.startswith("sink:")
            full_ctx = context_service.metadata(envelope.trace_id, full=True)
            if self.ordered_sink_mode == "source_seq" and is_sink_node:
                if not isinstance(full_ctx.get("__seq"), int):
                    raise ValueError(
                        f"Missing __seq in context for sink node '{node_name}' "
                        "while runtime.ordering.sink_mode=source_seq"
                    )
            raw_ctx = full_ctx if (node_name in self.full_context_nodes) else {
                key: value for key, value in full_ctx.items() if not key.startswith("__")
            }
            observability_ctx = dict(raw_ctx)
            if isinstance(envelope.span_id, str) and envelope.span_id:
                observability_ctx["__parent_span_id"] = envelope.span_id
            self._stamp_runner_gap(
                envelope=envelope,
                observability_ctx=observability_ctx,
            )
            self._enrich_observability_ctx(envelope, observability_ctx)
            node_ctx = dict(raw_ctx)
            node = self.nodes[node_name]
            observability_enabled = not SyncRunner._is_observability_system_node(node_name)
            observer_state = (
                await _maybe_await(
                    observability.before_node(
                        node_name=node_name,
                        payload=envelope.payload,
                        ctx=observability_ctx,
                        trace_id=envelope.trace_id,
                    )
                )
                if observability_enabled
                else None
            )
            try:
                outputs = await _coerce_node_outputs(node(envelope.payload, node_ctx))
            except Exception as exc:
                if observability_enabled:
                    error_service_outputs = await _maybe_await(
                        observability.on_node_error(
                            node_name=node_name,
                            payload=envelope.payload,
                            ctx=observability_ctx,
                            trace_id=envelope.trace_id,
                            error=exc,
                            state=observer_state,
                        )
                    )
                self._route_observability_service_outputs(
                    service_outputs=error_service_outputs,
                    source_node=node_name,
                    trace_id=envelope.trace_id,
                    reply_to=envelope.reply_to,
                    span_id=SyncRunner._span_id_from_observer_state(observer_state),
                    tombstone=envelope.tombstone,
                    work_queue=work_queue,
                    router=router,
                )
                raise
            produced_span_id = SyncRunner._span_id_from_observer_state(observer_state)
            if observability_enabled:
                after_service_outputs = await _maybe_await(
                    observability.after_node(
                        node_name=node_name,
                        payload=envelope.payload,
                        ctx=observability_ctx,
                        trace_id=envelope.trace_id,
                        outputs=outputs,
                        state=observer_state,
                    )
                )
                self._route_observability_service_outputs(
                    service_outputs=after_service_outputs,
                    source_node=node_name,
                    trace_id=envelope.trace_id,
                    reply_to=envelope.reply_to,
                    span_id=produced_span_id,
                    tombstone=envelope.tombstone,
                    work_queue=work_queue,
                    router=router,
                )

            for output in outputs:
                terminal = SyncRunner._terminal_event_from_output(output)
                if terminal is not None:
                    terminal_trace_id = output.trace_id if isinstance(output, Envelope) else None
                    resolved_trace_id = terminal_trace_id or envelope.trace_id
                    terminal_service_outputs = self._emit_terminal_event(
                        trace_id=resolved_trace_id,
                        terminal=terminal,
                    )
                    self._collect_terminal_output(
                        Envelope(
                            payload=terminal,
                            trace_id=resolved_trace_id,
                            reply_to=envelope.reply_to,
                            span_id=produced_span_id,
                        )
                    )
                    self._route_observability_service_outputs(
                        service_outputs=terminal_service_outputs,
                        source_node=node_name,
                        trace_id=resolved_trace_id,
                        reply_to=envelope.reply_to,
                        span_id=produced_span_id,
                        tombstone=bool(envelope.tombstone),
                        work_queue=work_queue,
                        router=router,
                    )
                    continue

                explicit_trace_id = output.trace_id if isinstance(output, Envelope) else None
                explicit_reply_to = output.reply_to if isinstance(output, Envelope) else None
                explicit_span_id = output.span_id if isinstance(output, Envelope) else None
                explicit_tombstone = output.tombstone if isinstance(output, Envelope) else False
                if self.allow_external_deliveries and isinstance(output, Envelope) and output.target is not None:
                    target_names = (
                        [output.target]
                        if isinstance(output.target, str)
                        else list(output.target)
                    )
                    downstream_tombstone = bool(explicit_tombstone or envelope.tombstone)
                    for target_name in target_names:
                        envelope_out = Envelope(
                            payload=output.payload,
                            target=target_name,
                            trace_id=explicit_trace_id or envelope.trace_id,
                            reply_to=explicit_reply_to or envelope.reply_to,
                            span_id=explicit_span_id or produced_span_id,
                            tombstone=downstream_tombstone,
                        )
                        if self.allow_external_deliveries and target_name not in self.nodes:
                            self._collect_external_delivery(envelope_out)
                            continue
                        work_queue.push(envelope_out)
                    continue
                try:
                    routing_result = router.route([output], source=node_name)
                except ValueError as exc:
                    if self.allow_external_deliveries and "No consumers registered" in str(exc):
                        self._collect_terminal_output(
                            Envelope(
                                payload=output.payload if isinstance(output, Envelope) else output,
                                trace_id=explicit_trace_id or envelope.trace_id,
                                reply_to=explicit_reply_to or envelope.reply_to,
                                span_id=explicit_span_id or produced_span_id,
                                tombstone=bool(explicit_tombstone or envelope.tombstone),
                            )
                        )
                        continue
                    raise
                downstream_trace_id = explicit_trace_id or envelope.trace_id
                downstream_reply_to = explicit_reply_to or envelope.reply_to
                downstream_span_id = explicit_span_id or produced_span_id
                downstream_tombstone = bool(explicit_tombstone or envelope.tombstone)
                for target_name, payload in SyncRunner._local_deliveries(routing_result):
                    envelope_out = Envelope(
                        payload=payload,
                        target=target_name,
                        trace_id=downstream_trace_id,
                        reply_to=downstream_reply_to,
                        span_id=downstream_span_id,
                        tombstone=downstream_tombstone,
                    )
                    if self.allow_external_deliveries and target_name not in self.nodes:
                        self._collect_external_delivery(envelope_out)
                        continue
                    work_queue.push(envelope_out)

    async def run_until_stopped_async(
        self,
        *,
        poll_timeout_seconds: float = 0.01,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        work_queue = self._work_queue()
        poll_timeout = max(0.0, float(poll_timeout_seconds))
        idle_deadline = (
            time.monotonic() + float(idle_timeout_seconds)
            if isinstance(idle_timeout_seconds, (int, float)) and float(idle_timeout_seconds) > 0
            else None
        )
        while True:
            await self.run_async()
            if self._stop_requested:
                size_fn = getattr(work_queue, "size", None)
                size = size_fn() if callable(size_fn) else 0
                if not self.drain_on_stop or size == 0:
                    return
            if callable(getattr(work_queue, "is_closed", None)) and bool(work_queue.is_closed()):
                if work_queue.size() == 0:
                    return
            now = time.monotonic()
            if idle_deadline is not None and now >= idle_deadline and work_queue.size() == 0:
                return
            if poll_timeout > 0:
                await asyncio.sleep(poll_timeout)
            else:
                await asyncio.sleep(0)
            if idle_deadline is not None and work_queue.size() > 0:
                idle_deadline = time.monotonic() + float(idle_timeout_seconds)
                continue
            if idle_deadline is not None and work_queue.size() == 0 and time.monotonic() >= idle_deadline:
                return

    def request_stop(self) -> None:
        # Graceful stop signal: runner finishes inflight queue when drain_on_stop=True.
        self._stop_requested = True

    def on_run_end(self) -> None:
        _run_async_blocking(_maybe_await(self._observability().on_run_end()))

    def _context_service(self) -> ContextService:
        return SyncRunner._context_service(self)  # type: ignore[misc]

    def _work_queue(self) -> QueuePort:
        if isinstance(self.work_queue, QueuePort):
            return self.work_queue
        if callable(getattr(self.work_queue, "push", None)) and callable(
            getattr(self.work_queue, "pop", None)
        ) and callable(getattr(self.work_queue, "size", None)):
            return self.work_queue  # type: ignore[return-value]
        raise ValueError("AsyncRunner work_queue is not resolved via DI")

    def _router(self) -> RoutingService:
        return SyncRunner._router(self)  # type: ignore[misc]

    def _observability(self) -> ObservabilityPipelineService:
        return SyncRunner._observability(self)  # type: ignore[misc]

    def _emit_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> object | None:
        return self._observability().on_ingress(trace_id=trace_id, reply_to=reply_to)

    def _emit_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal: TerminalEvent | None,
    ) -> object | None:
        return self._observability().on_terminal_event(trace_id=trace_id, terminal_event=terminal)

    def __post_init__(self) -> None:
        if self.ordered_sink_mode not in _ORDERED_SINK_MODES:
            raise ValueError(
                "AsyncRunner ordered_sink_mode must be one of: "
                f"{sorted(_ORDERED_SINK_MODES)}"
            )

    @staticmethod
    def _coerce_observability_service_outputs(candidate: object) -> list[object]:
        return SyncRunner._coerce_observability_service_outputs(candidate)

    @staticmethod
    def _normalize_observability_output(
        *,
        output: object,
        source_node: str,
        trace_id: str | None,
    ) -> object:
        return SyncRunner._normalize_observability_output(
            output=output,
            source_node=source_node,
            trace_id=trace_id,
        )

    def _route_observability_service_outputs(
        self,
        *,
        service_outputs: object,
        source_node: str,
        trace_id: str | None,
        reply_to: str | None,
        span_id: str | None,
        tombstone: bool = False,
        work_queue: QueuePort,
        router: RoutingService,
    ) -> None:
        SyncRunner._route_observability_service_outputs(
            self,
            service_outputs=service_outputs,
            source_node=source_node,
            trace_id=trace_id,
            reply_to=reply_to,
            span_id=span_id,
            tombstone=tombstone,
            work_queue=work_queue,
            router=router,
        )

    def _collect_external_delivery(self, envelope: Envelope) -> None:
        if isinstance(self.external_deliveries, list):
            self.external_deliveries.append(envelope)
        if isinstance(self.boundary_outputs, list):
            self.boundary_outputs.append(envelope)

    def _collect_terminal_output(self, envelope: Envelope) -> None:
        if isinstance(self.terminal_outputs, list):
            self.terminal_outputs.append(envelope)
        if isinstance(self.boundary_outputs, list):
            self.boundary_outputs.append(envelope)

    def _enrich_observability_ctx(self, envelope: Envelope, observability_ctx: dict[str, object]) -> None:
        enrich = self.observability_context_enricher
        if not callable(enrich):
            return
        extra = enrich(envelope, dict(observability_ctx))
        if isinstance(extra, dict):
            observability_ctx.update(extra)

    def _stamp_runner_gap(self, *, envelope: Envelope, observability_ctx: dict[str, object]) -> None:
        trace_id = envelope.trace_id
        if not isinstance(trace_id, str) or not trace_id:
            return
        now = time.monotonic()
        prev = self._last_seen_by_trace.get(trace_id)
        self._last_seen_by_trace[trace_id] = now
        if prev is None:
            return
        observability_ctx["__runner_gap_ms"] = (now - prev) * 1000.0


async def _coerce_node_outputs(raw: object) -> list[object]:
    resolved = await _maybe_await(raw)
    if inspect.isasyncgen(resolved):
        items: list[object] = []
        async for item in resolved:
            items.append(item)
        return items
    return list(resolved)


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _run_async_blocking(coro: object) -> object:
    if not asyncio.iscoroutine(coro):
        raise TypeError("expected coroutine")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result_box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - preserve original exception.
            error_box["error"] = exc

    import threading

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")
