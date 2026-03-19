from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Condition, Thread

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.kernel.context import Context
from stream_kernel.kernel.trace import ErrorInfo, RouteInfo, TraceRecorder, TraceSpan, TraceRecord
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.domain.monitoring import MonitoringMessage
from stream_kernel.observability.domain.telemetry import TelemetryMessage
from stream_kernel.observability.events import (
    DebugDispatchEvent,
    LogDispatchEvent,
    MetricDispatchEvent,
    MonitorDispatchEvent,
    TraceDispatchEvent,
)
from stream_kernel.platform.services.messaging.reply_coordinator import ReplyCoordinatorService
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.observability import ObservabilityPipelineService


@dataclass(slots=True)
class _NodeObservationState:
    started_at_monotonic: float
    trace_ctx: Context | None = None
    trace_span: TraceSpan | None = None
    previous_trace_exit: datetime | None = None


@dataclass(slots=True, frozen=True)
class _QueuedDispatch:
    channel: str
    event: object
    trace_id: str | None
    attributes: dict[str, object] | None


@service(name="dispatching_observability_service")
@dataclass(slots=True)
class DispatchingObservabilityService(ObservabilityPipelineService):
    # Platform-native observability service:
    # - root/leaf (process_supervisor): emits DispatchEvent objects into runner queue;
    # - observability worker (and non-supervisor profiles): writes directly to exporter adapters.
    runtime: dict[str, object] = field(default_factory=dict)
    trace_sinks: list[object] = field(default_factory=list)
    log_sinks: list[object] = field(default_factory=list)
    telemetry_sinks: list[object] = field(default_factory=list)
    monitoring_sinks: list[object] = field(default_factory=list)
    debug_sinks: list[object] = field(default_factory=list)
    reply_coordinator: object = inject.service(ReplyCoordinatorService)
    _trace_recorder: TraceRecorder = field(
        default_factory=lambda: TraceRecorder(
            signature_mode="type_only",
            context_diff_mode="none",
            context_diff_whitelist=(),
        )
    )
    _trace_contexts: dict[str, Context] = field(default_factory=dict)
    _dispatch_queue_enabled: bool = field(default=False, init=False, repr=False)
    _dispatch_queue_max_items: int = field(default=131072, init=False, repr=False)
    _dispatch_queue_drop_policy: str = field(default="non_block", init=False, repr=False)
    _dispatch_queue_block_timeout_ms: int = field(default=100, init=False, repr=False)
    _dispatch_queue_batch_max_items: int = field(default=100, init=False, repr=False)
    _dispatch_queue_flush_interval_seconds: float = field(default=0.02, init=False, repr=False)
    _dispatch_queue_drain_timeout_seconds: float = field(default=30.0, init=False, repr=False)
    _dispatch_queue: deque[_QueuedDispatch] = field(default_factory=deque, init=False, repr=False)
    _dispatch_condition: Condition = field(default_factory=Condition, init=False, repr=False)
    _dispatch_thread: Thread | None = field(default=None, init=False, repr=False)
    _dispatch_stopping: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.runtime = dict(self.runtime)
        self.trace_sinks = list(self.trace_sinks)
        self.log_sinks = list(self.log_sinks)
        self.telemetry_sinks = list(self.telemetry_sinks)
        self.monitoring_sinks = list(self.monitoring_sinks)
        self.debug_sinks = list(self.debug_sinks)
        self._configure_dispatch_queue()
        if self._dispatch_queue_enabled:
            self._start_dispatch_worker()

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        state = _NodeObservationState(started_at_monotonic=time.monotonic())
        if not isinstance(trace_id, str) or not trace_id:
            return state
        trace_ctx = self._trace_contexts.setdefault(
            trace_id,
            Context(
                trace_id=trace_id,
                run_id=str(ctx.get("__run_id", "run")),
                scenario_id=str(ctx.get("__scenario_id", "scenario")),
                received_at=datetime.now(tz=UTC),
            ),
        )
        previous_trace_exit: datetime | None = None
        if trace_ctx.trace:
            last_record = trace_ctx.trace[-1]
            last_exit = getattr(last_record, "t_exit", None)
            if isinstance(last_exit, datetime):
                previous_trace_exit = last_exit
        span = self._trace_recorder.begin(
            ctx=trace_ctx,
            step_name=node_name,
            step_index=int(ctx.get("__step_index", -1)) if isinstance(ctx.get("__step_index"), int) else -1,
            work_index=0,
            msg_in=payload,
            route=_route_info_from_ctx(ctx),
        )
        state.trace_ctx = trace_ctx
        state.trace_span = span
        state.previous_trace_exit = previous_trace_exit
        return state

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
        duration_ms = _duration_ms(state)
        events: list[object] = []
        trace_record = self._finish_trace_record(
            node_name=node_name,
            outputs=outputs,
            state=state,
            error=None,
        )
        if trace_record is not None:
            events.extend(
                self._emit_or_wrap_trace(
                    event=trace_record,
                    trace_id=trace_id,
                    source_node=node_name,
                )
            )
        if self._runner_node_logs_enabled() and not self._is_log_relay_payload(payload):
            log_message = LogMessage(
                level="info",
                message=f"node '{node_name}' processed",
                fields={
                    "event": "runner.node.after",
                    "node_name": node_name,
                    "status": "ok",
                    "duration_ms": duration_ms,
                    "outputs_count": len(outputs),
                    "trace_id": trace_id,
                },
            )
            events.extend(self._emit_or_wrap_log(event=log_message, trace_id=trace_id, source_node=node_name))

        metric_message = TelemetryMessage(
            metric="runner.node.duration_ms",
            value=duration_ms,
            tags={
                "node_name": node_name,
                "status": "ok",
            },
        )
        events.extend(self._emit_or_wrap_metric(event=metric_message, trace_id=trace_id, source_node=node_name))
        runner_gap_ms = _runner_gap_ms_from_ctx(ctx)
        if runner_gap_ms is not None:
            events.extend(
                self._emit_or_wrap_metric(
                    event=TelemetryMessage(
                        metric="runner.node.inter_activation_gap_ms",
                        value=runner_gap_ms,
                        tags={
                            "node_name": node_name,
                            "status": "ok",
                        },
                    ),
                    trace_id=trace_id,
                    source_node=node_name,
                )
            )

        monitor_message = MonitoringMessage(
            name="runner.node",
            status="ok",
            details={
                "node_name": node_name,
                "duration_ms": duration_ms,
                "outputs_count": len(outputs),
            },
        )
        events.extend(
            self._emit_or_wrap_monitoring(
                event=monitor_message,
                trace_id=trace_id,
                source_node=node_name,
            )
        )
        return events or None

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
        duration_ms = _duration_ms(state)
        events: list[object] = []
        trace_record = self._finish_trace_record(
            node_name=node_name,
            outputs=[],
            state=state,
            error=error,
        )
        if trace_record is not None:
            events.extend(
                self._emit_or_wrap_trace(
                    event=trace_record,
                    trace_id=trace_id,
                    source_node=node_name,
                )
            )
        if self._runner_node_logs_enabled() and not self._is_log_relay_payload(payload):
            log_message = LogMessage(
                level="error",
                message=f"node '{node_name}' failed",
                fields={
                    "event": "runner.node.error",
                    "node_name": node_name,
                    "status": "error",
                    "duration_ms": duration_ms,
                    "trace_id": trace_id,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            events.extend(self._emit_or_wrap_log(event=log_message, trace_id=trace_id, source_node=node_name))

        metric_message = TelemetryMessage(
            metric="runner.node.errors_total",
            value=1,
            tags={
                "node_name": node_name,
                "status": "error",
            },
        )
        events.extend(self._emit_or_wrap_metric(event=metric_message, trace_id=trace_id, source_node=node_name))

        monitor_message = MonitoringMessage(
            name="runner.node",
            status="error",
            details={
                "node_name": node_name,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )
        events.extend(
            self._emit_or_wrap_monitoring(
                event=monitor_message,
                trace_id=trace_id,
                source_node=node_name,
            )
        )
        return events or None

    def on_run_end(self) -> None:
        self._stop_dispatch_worker()
        for sink in [
            *self.trace_sinks,
            *self.log_sinks,
            *self.telemetry_sinks,
            *self.monitoring_sinks,
            *self.debug_sinks,
        ]:
            flush = getattr(sink, "flush", None)
            if callable(flush):
                try:
                    flush()
                except Exception:
                    pass
            close = getattr(sink, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.emit_trace_event(event=event, trace_id=trace_id, attributes=attributes)

    def emit_trace_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        sink_errors = self._emit_many(self.trace_sinks, event)
        return self._sink_error_events(
            channel="trace",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    async def emit_trace_event_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        sink_errors = await self._emit_many_async(self.trace_sinks, event)
        return self._sink_error_events(
            channel="trace",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.emit_log_event(event=event, trace_id=trace_id, attributes=attributes)

    def emit_log_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        _ = (trace_id, attributes)
        normalized = self._coerce_log_message(event)
        # Do not emit recursive log events when log sinks fail.
        self._emit_many(self.log_sinks, normalized)
        return []

    async def emit_log_event_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        _ = (trace_id, attributes)
        normalized = self._coerce_log_message(event)
        await self._emit_many_async(self.log_sinks, normalized)
        return []

    def publish_debug(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.emit_debug_event(event=event, trace_id=trace_id, attributes=attributes)

    def emit_debug_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        _ = (trace_id, attributes)
        normalized = self._coerce_debug_message(event)
        self._emit_many(self.debug_sinks, normalized)
        return []

    async def emit_debug_event_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        _ = (trace_id, attributes)
        normalized = self._coerce_debug_message(event)
        await self._emit_many_async(self.debug_sinks, normalized)
        return []

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.emit_metric_event(event=event, trace_id=trace_id, attributes=attributes)

    def emit_metric_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        normalized = self._coerce_metric_message(event)
        sink_errors = self._emit_many(self.telemetry_sinks, normalized)
        return self._sink_error_events(
            channel="metric",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    async def emit_metric_event_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        normalized = self._coerce_metric_message(event)
        sink_errors = await self._emit_many_async(self.telemetry_sinks, normalized)
        return self._sink_error_events(
            channel="metric",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.emit_monitoring_event(
            event=event,
            trace_id=trace_id,
            attributes=attributes,
        )

    def emit_monitoring_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        normalized = self._coerce_monitoring_message(event)
        sink_errors = self._emit_many(self.monitoring_sinks, normalized)
        return self._sink_error_events(
            channel="monitor",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    async def emit_monitoring_event_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        normalized = self._coerce_monitoring_message(event)
        sink_errors = await self._emit_many_async(self.monitoring_sinks, normalized)
        return self._sink_error_events(
            channel="monitor",
            trace_id=trace_id,
            attributes=attributes,
            sink_errors=sink_errors,
        )

    def submit_trace_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        if not self._dispatch_queue_enabled:
            return self.emit_trace_event(event=event, trace_id=trace_id, attributes=attributes)
        self._enqueue_dispatch(
            _QueuedDispatch(
                channel="trace",
                event=event,
                trace_id=trace_id,
                attributes=dict(attributes) if isinstance(attributes, dict) else None,
            )
        )
        return []

    def submit_log_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        if not self._dispatch_queue_enabled:
            return self.emit_log_event(event=event, trace_id=trace_id, attributes=attributes)
        self._enqueue_dispatch(
            _QueuedDispatch(
                channel="log",
                event=event,
                trace_id=trace_id,
                attributes=dict(attributes) if isinstance(attributes, dict) else None,
            )
        )
        return []

    def submit_metric_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        if not self._dispatch_queue_enabled:
            return self.emit_metric_event(event=event, trace_id=trace_id, attributes=attributes)
        self._enqueue_dispatch(
            _QueuedDispatch(
                channel="metric",
                event=event,
                trace_id=trace_id,
                attributes=dict(attributes) if isinstance(attributes, dict) else None,
            )
        )
        return []

    def submit_monitoring_event(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> list[object]:
        if not self._dispatch_queue_enabled:
            return self.emit_monitoring_event(event=event, trace_id=trace_id, attributes=attributes)
        self._enqueue_dispatch(
            _QueuedDispatch(
                channel="monitor",
                event=event,
                trace_id=trace_id,
                attributes=dict(attributes) if isinstance(attributes, dict) else None,
            )
        )
        return []

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> object | None:
        self._reply_coordinator().register_if_requested(trace_id=trace_id, reply_to=reply_to)
        monitor = MonitoringMessage(
            name="runner.ingress",
            status="accepted",
            details={"reply_requested": bool(reply_to)},
        )
        return self._emit_or_wrap_monitoring(
            event=monitor,
            trace_id=trace_id,
            source_node="__ingress__",
        ) or None

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> object | None:
        self._reply_coordinator().complete_if_waiting(
            trace_id=trace_id,
            terminal_event=terminal_event,
        )
        if terminal_event is None:
            return None
        monitor = MonitoringMessage(
            name="runner.terminal_event",
            status=terminal_event.status,
            details={
                "has_payload": terminal_event.payload is not None,
                "error": terminal_event.error,
            },
        )
        return self._emit_or_wrap_monitoring(
            event=monitor,
            trace_id=trace_id,
            source_node="__terminal__",
        ) or None

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        monitor = MonitoringMessage(
            name="runner.ingress_rate_limit",
            status="allowed" if allowed else "rejected",
            details={
                "source_node": source_node,
                "source_role": source_role,
                "limiter_profile": limiter_profile,
            },
        )
        self._emit_or_wrap_monitoring(
            event=monitor,
            trace_id=trace_id,
            source_node=source_node or "__ingress__",
        )

    def on_outbound_policy_decision(
        self,
        *,
        stage: str,
        decision: str,
        trace_id: str | None,
        key: str | None,
        profile: str | None,
        attempt: int,
    ) -> None:
        monitor = MonitoringMessage(
            name="runner.outbound_policy",
            status=decision,
            details={
                "stage": stage,
                "key": key,
                "profile": profile,
                "attempt": attempt,
            },
        )
        self._emit_or_wrap_monitoring(
            event=monitor,
            trace_id=trace_id,
            source_node="__outbound_policy__",
        )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        message = LogMessage(
            level="info",
            message=f"runtime lifecycle: {event}",
            fields={
                "event": "runtime.lifecycle",
                "runtime_event": event,
                "process_group": process_group,
                **dict(details or {}),
            },
        )
        self._emit_or_wrap_log(event=message, trace_id=None, source_node="__runtime_lifecycle__")

    def on_runtime_debug_messages(
        self,
        *,
        messages: list[DebugMessage],
        source_node: str,
        trace_id: str | None = None,
    ) -> list[object]:
        outputs: list[object] = []
        for message in messages:
            outputs.extend(
                self._emit_or_wrap_debug(
                    event=message,
                    trace_id=trace_id,
                    source_node=source_node,
                )
            )
        return outputs

    def _finish_trace_record(
        self,
        *,
        node_name: str,
        outputs: list[object],
        state: object | None,
        error: Exception | None,
    ) -> TraceRecord | None:
        if not isinstance(state, _NodeObservationState):
            return None
        if state.trace_ctx is None or state.trace_span is None:
            return None
        trace_error = (
            ErrorInfo(type=type(error).__name__, message=str(error), where=node_name, stack=None)
            if isinstance(error, Exception)
            else None
        )
        record = self._trace_recorder.finish(
            ctx=state.trace_ctx,
            span=state.trace_span,
            msg_out=outputs,
            status="error" if isinstance(error, Exception) else "ok",
            error=trace_error,
        )
        return _record_with_prev_trace_gap(
            record=record,
            previous_trace_exit=state.previous_trace_exit,
        )

    def _emit_or_wrap_trace(
        self,
        *,
        event: object,
        trace_id: str | None,
        source_node: str,
    ) -> list[object]:
        if not self._channel_enabled("trace"):
            return []
        if self._should_emit_dispatch_events():
            return [
                TraceDispatchEvent(
                    payload=event,
                    trace_id=trace_id,
                    attributes={"source_node": source_node},
                )
            ]
        self.publish_trace(event=event, trace_id=trace_id, attributes={"source_node": source_node})
        return []

    def _emit_or_wrap_log(
        self,
        *,
        event: object,
        trace_id: str | None,
        source_node: str,
    ) -> list[object]:
        if not self._channel_enabled("log"):
            return []
        if self._should_emit_dispatch_events():
            return [
                LogDispatchEvent(
                    payload=event,
                    trace_id=trace_id,
                    attributes={"source_node": source_node},
                )
            ]
        self.publish_log(event=event, trace_id=trace_id, attributes={"source_node": source_node})
        return []

    def _emit_or_wrap_debug(
        self,
        *,
        event: object,
        trace_id: str | None,
        source_node: str,
    ) -> list[object]:
        if not self._channel_enabled("debug"):
            return []
        if self._should_emit_dispatch_events() and not self.debug_sinks:
            return [
                DebugDispatchEvent(
                    payload=self._coerce_debug_message(event),
                    trace_id=trace_id,
                    attributes={"source_node": source_node},
                )
            ]
        self.publish_debug(event=event, trace_id=trace_id, attributes={"source_node": source_node})
        return []

    def _emit_or_wrap_metric(
        self,
        *,
        event: object,
        trace_id: str | None,
        source_node: str,
    ) -> list[object]:
        if not self._channel_enabled("metric"):
            return []
        if self._should_emit_dispatch_events():
            return [
                MetricDispatchEvent(
                    payload=event,
                    trace_id=trace_id,
                    attributes={"source_node": source_node},
                )
            ]
        self.publish_metric(event=event, trace_id=trace_id, attributes={"source_node": source_node})
        return []

    def _emit_or_wrap_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None,
        source_node: str,
    ) -> list[object]:
        if not self._channel_enabled("monitor"):
            return []
        if self._should_emit_dispatch_events():
            return [
                MonitorDispatchEvent(
                    payload=event,
                    trace_id=trace_id,
                    attributes={"source_node": source_node},
                )
            ]
        self.publish_monitoring(event=event, trace_id=trace_id, attributes={"source_node": source_node})
        return []

    def _emit_many(self, sinks: list[object], payload: object) -> list[tuple[str, Exception]]:
        errors: list[tuple[str, Exception]] = []
        for sink in sinks:
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(payload)
                except Exception as exc:  # noqa: BLE001 - observability channel must not break business path.
                    errors.append((type(sink).__name__, exc))
        return errors

    async def _emit_many_async(self, sinks: list[object], payload: object) -> list[tuple[str, Exception]]:
        errors: list[tuple[str, Exception]] = []
        pending: list[tuple[str, object]] = []
        for sink in sinks:
            sink_name = type(sink).__name__
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(payload)
                    if inspect.isawaitable(result):
                        pending.append((sink_name, result))
                        continue
                except Exception as exc:  # noqa: BLE001 - observability channel must not break business path.
                    errors.append((sink_name, exc))
                    continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(payload)
                except Exception as exc:  # noqa: BLE001 - observability channel must not break business path.
                    errors.append((sink_name, exc))
        if pending:
            results = await asyncio.gather(
                *(awaitable for _name, awaitable in pending),
                return_exceptions=True,
            )
            for (sink_name, _awaitable), result in zip(pending, results, strict=False):
                if isinstance(result, Exception):
                    errors.append((sink_name, result))
        return errors

    def _configure_dispatch_queue(self) -> None:
        observability = self.runtime.get("observability", {})
        if not isinstance(observability, dict):
            self._dispatch_queue_enabled = False
            return
        tracing = observability.get("tracing", {})
        if not isinstance(tracing, dict):
            self._dispatch_queue_enabled = False
            return
        queue_cfg = tracing.get("dispatch_queue", {})
        if not isinstance(queue_cfg, dict):
            self._dispatch_queue_enabled = False
            return
        max_items = queue_cfg.get("max_items", 131072)
        if isinstance(max_items, int) and max_items > 0:
            self._dispatch_queue_max_items = int(max_items)
        drop_policy = queue_cfg.get("drop_policy", "non_block")
        if isinstance(drop_policy, str) and drop_policy:
            self._dispatch_queue_drop_policy = drop_policy
        block_timeout_ms = queue_cfg.get("block_timeout_ms", 100)
        if isinstance(block_timeout_ms, int) and block_timeout_ms > 0:
            self._dispatch_queue_block_timeout_ms = int(block_timeout_ms)
        forward_batch_max_items = queue_cfg.get("forward_batch_max_items", 100)
        if isinstance(forward_batch_max_items, int) and forward_batch_max_items > 0:
            self._dispatch_queue_batch_max_items = int(forward_batch_max_items)
        forward_flush_interval_ms = queue_cfg.get("forward_flush_interval_ms", 20)
        if isinstance(forward_flush_interval_ms, int) and forward_flush_interval_ms > 0:
            self._dispatch_queue_flush_interval_seconds = float(forward_flush_interval_ms) / 1000.0
        drain_timeout_seconds = queue_cfg.get("drain_timeout_seconds", 30.0)
        if isinstance(drain_timeout_seconds, (int, float)) and float(drain_timeout_seconds) > 0:
            self._dispatch_queue_drain_timeout_seconds = float(drain_timeout_seconds)
        # Queue is always enabled for configured dispatch_queue: this keeps
        # observability nodes non-blocking and shifts exporter latency off loop.
        self._dispatch_queue_enabled = True

    def _start_dispatch_worker(self) -> None:
        with self._dispatch_condition:
            if isinstance(self._dispatch_thread, Thread) and self._dispatch_thread.is_alive():
                return
            self._dispatch_stopping = False
            self._dispatch_thread = Thread(
                name="sk-observability-dispatch",
                target=self._dispatch_loop,
                daemon=True,
            )
            self._dispatch_thread.start()

    def _stop_dispatch_worker(self) -> None:
        thread: Thread | None = None
        with self._dispatch_condition:
            if not isinstance(self._dispatch_thread, Thread):
                return
            self._dispatch_stopping = True
            self._dispatch_condition.notify_all()
            thread = self._dispatch_thread
        if isinstance(thread, Thread) and thread.is_alive():
            thread.join(timeout=max(0.1, float(self._dispatch_queue_drain_timeout_seconds)))
        with self._dispatch_condition:
            self._dispatch_thread = None

    def _enqueue_dispatch(self, item: _QueuedDispatch) -> None:
        with self._dispatch_condition:
            if self._dispatch_stopping:
                return
            queue_max = max(1, int(self._dispatch_queue_max_items))
            if self._dispatch_queue_drop_policy == "drop_newest":
                if len(self._dispatch_queue) >= queue_max:
                    return
            elif self._dispatch_queue_drop_policy == "drop_oldest":
                if len(self._dispatch_queue) >= queue_max:
                    self._dispatch_queue.popleft()
            elif self._dispatch_queue_drop_policy == "block_with_timeout":
                deadline = time.monotonic() + (float(self._dispatch_queue_block_timeout_ms) / 1000.0)
                while len(self._dispatch_queue) >= queue_max and not self._dispatch_stopping:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    self._dispatch_condition.wait(timeout=min(remaining, 0.01))
            # non_block/no-drop mode: do not reject items, allow in-memory growth.
            self._dispatch_queue.append(item)
            self._dispatch_condition.notify_all()

    def _dispatch_loop(self) -> None:
        flush_interval = max(0.001, float(self._dispatch_queue_flush_interval_seconds))
        while True:
            batch = self._dequeue_dispatch_batch(
                max_items=max(1, int(self._dispatch_queue_batch_max_items)),
                timeout_seconds=flush_interval,
            )
            if batch:
                self._dispatch_batch(batch)
                continue
            with self._dispatch_condition:
                if self._dispatch_stopping and not self._dispatch_queue:
                    return

    def _dequeue_dispatch_batch(self, *, max_items: int, timeout_seconds: float) -> list[_QueuedDispatch]:
        with self._dispatch_condition:
            if not self._dispatch_queue and not self._dispatch_stopping:
                self._dispatch_condition.wait(timeout=timeout_seconds)
            if not self._dispatch_queue:
                return []
            take = min(max_items, len(self._dispatch_queue))
            batch: list[_QueuedDispatch] = []
            for _ in range(take):
                batch.append(self._dispatch_queue.popleft())
            self._dispatch_condition.notify_all()
            return batch

    def _dispatch_batch(self, batch: list[_QueuedDispatch]) -> None:
        for item in batch:
            channel = item.channel
            if channel == "trace":
                _ = self.emit_trace_event(
                    event=item.event,
                    trace_id=item.trace_id,
                    attributes=item.attributes,
                )
                continue
            if channel == "log":
                _ = self.emit_log_event(
                    event=item.event,
                    trace_id=item.trace_id,
                    attributes=item.attributes,
                )
                continue
            if channel == "metric":
                _ = self.emit_metric_event(
                    event=item.event,
                    trace_id=item.trace_id,
                    attributes=item.attributes,
                )
                continue
            if channel == "monitor":
                _ = self.emit_monitoring_event(
                    event=item.event,
                    trace_id=item.trace_id,
                    attributes=item.attributes,
                )
                continue

    def _sink_error_events(
        self,
        *,
        channel: str,
        trace_id: str | None,
        attributes: dict[str, object] | None,
        sink_errors: list[tuple[str, Exception]],
    ) -> list[object]:
        if not sink_errors:
            return []
        if channel == "log" or not self._channel_enabled("log"):
            return []
        message = self._build_sink_error_log_message(
            channel=channel,
            sink_errors=sink_errors,
            source_node=self._source_node_from_attributes(attributes),
        )
        source_node = self._source_node_from_attributes(attributes)
        if self._should_emit_dispatch_events():
            return [
                LogDispatchEvent(
                    payload=message,
                    trace_id=trace_id,
                    attributes={
                        "source_node": source_node,
                        "origin_channel": channel,
                    },
                )
            ]
        self._emit_many(self.log_sinks, message)
        return []

    @staticmethod
    def _source_node_from_attributes(attributes: dict[str, object] | None) -> str:
        if isinstance(attributes, dict):
            candidate = attributes.get("source_node")
            if isinstance(candidate, str) and candidate:
                return candidate
        return "__observability_sink__"

    def _build_sink_error_log_message(
        self,
        *,
        channel: str,
        sink_errors: list[tuple[str, Exception]],
        source_node: str,
    ) -> LogMessage:
        first_sink, first_exc = sink_errors[0]
        fields: dict[str, object] = {
            "event": "observability.sink.emit_failed",
            "channel": channel,
            "source_node": source_node,
            "sink_failures_count": len(sink_errors),
            "first_sink": first_sink,
            "first_error_type": type(first_exc).__name__,
            "first_error_message": str(first_exc),
        }
        for index, (sink_name, exc) in enumerate(sink_errors[:3]):
            fields[f"failure_{index}_sink"] = sink_name
            fields[f"failure_{index}_error_type"] = type(exc).__name__
            fields[f"failure_{index}_error_message"] = str(exc)
        return LogMessage(
            level="error",
            message=f"observability sink emit failed on '{channel}' channel",
            fields=fields,
        )

    @staticmethod
    def _coerce_log_message(event: object) -> LogMessage:
        if isinstance(event, LogMessage):
            return event
        return LogMessage(
            level="info",
            message=f"log_event:{type(event).__name__}",
            fields={"value_type": type(event).__name__},
        )

    @staticmethod
    def _is_log_relay_payload(payload: object) -> bool:
        # Do not emit runner.node.* log records when the processed payload itself
        # is a logging message/event. This prevents log-on-log feedback loops on
        # relay nodes such as system.lifecycle.log_dispatch.
        return isinstance(payload, (LogMessage, LogDispatchEvent))

    @staticmethod
    def _coerce_metric_message(event: object) -> TelemetryMessage:
        if isinstance(event, TelemetryMessage):
            return event
        return TelemetryMessage(metric="runner.metric", value=1, tags={"value_type": type(event).__name__})

    @staticmethod
    def _coerce_monitoring_message(event: object) -> MonitoringMessage:
        if isinstance(event, MonitoringMessage):
            return event
        return MonitoringMessage(
            name="runner.monitoring",
            status="accepted",
            details={"value_type": type(event).__name__},
        )

    @staticmethod
    def _coerce_debug_message(event: object) -> DebugMessage:
        if isinstance(event, DebugMessage):
            return event
        return DebugMessage(
            timestamp=datetime.now(tz=UTC),
            event="runtime.debug.coerced",
            source="dispatching_observability_service",
            fields={"value_type": type(event).__name__},
            run_id=None,
            run_instance_id=None,
            process_group=None,
            worker_id=None,
            trace_id=None,
        )

    def _should_emit_dispatch_events(self) -> bool:
        role = self.runtime.get("__process_role")
        if isinstance(role, str) and role == "observability_worker":
            return False
        platform = self.runtime.get("platform", {})
        if not isinstance(platform, dict):
            return False
        bootstrap = platform.get("bootstrap", {})
        if not isinstance(bootstrap, dict) or bootstrap.get("mode") != "process_supervisor":
            return False
        observability = self.runtime.get("observability", {})
        if not isinstance(observability, dict):
            return False
        service_process = observability.get("service_process")
        if not isinstance(service_process, dict) or not service_process:
            service_process = observability.get("service_worker", {})
            if not isinstance(service_process, dict):
                return False
        return service_process.get("enabled") is True

    def _runner_node_logs_enabled(self) -> bool:
        observability = self.runtime.get("observability", {})
        if not isinstance(observability, dict):
            return False
        logging_cfg = observability.get("logging", {})
        if not isinstance(logging_cfg, dict):
            return False
        flag = logging_cfg.get("runner_node_events", False)
        return isinstance(flag, bool) and flag

    def _channel_enabled(self, channel: str) -> bool:
        kind_by_channel = {
            "trace": "system.obs.trace_dispatch",
            "log": "system.obs.log_dispatch",
            "metric": "system.obs.metric_dispatch",
            "monitor": "system.obs.monitor_dispatch",
            "debug": "system.obs.debug_dispatch",
        }
        section_by_channel = {
            "trace": "tracing",
            "log": "logging",
            "metric": "telemetry",
            "monitor": "monitoring",
            "debug": "logging",
        }
        observability = self.runtime.get("observability", {})
        if not isinstance(observability, dict):
            return False
        pipeline_cfg = observability.get("pipeline")
        if isinstance(pipeline_cfg, dict):
            system_nodes = pipeline_cfg.get("system_nodes")
            if isinstance(system_nodes, list):
                expected_kind = kind_by_channel.get(channel)
                if not isinstance(expected_kind, str):
                    return False
                for item in system_nodes:
                    if not isinstance(item, dict):
                        continue
                    if item.get("kind") != expected_kind:
                        continue
                    enabled = item.get("enabled", True)
                    return not isinstance(enabled, bool) or enabled
                return False
        section = section_by_channel.get(channel)
        if not isinstance(section, str):
            return False
        section_cfg = observability.get(section, {})
        if not isinstance(section_cfg, dict):
            return False
        exporters = section_cfg.get("exporters")
        if not isinstance(exporters, list):
            return False
        if channel == "debug":
            return any(
                isinstance(item, dict)
                and item.get("enabled", True) is not False
                and item.get("kind") == "redis_debug"
                for item in exporters
            )
        return any(
            isinstance(item, dict) and item.get("enabled", True) is not False
            for item in exporters
        )

    def _reply_coordinator(self) -> ReplyCoordinatorService:
        if isinstance(self.reply_coordinator, ReplyCoordinatorService):
            return self.reply_coordinator
        if (
            callable(getattr(self.reply_coordinator, "register_if_requested", None))
            and callable(getattr(self.reply_coordinator, "complete_if_waiting", None))
        ):
            return self.reply_coordinator  # type: ignore[return-value]
        raise ValueError("DispatchingObservabilityService reply_coordinator is not resolved via DI")


def _duration_ms(state: object | None) -> float:
    if not isinstance(state, _NodeObservationState):
        return 0.0
    return max((time.monotonic() - state.started_at_monotonic) * 1000.0, 0.0)


def _runner_gap_ms_from_ctx(ctx: dict[str, object]) -> float | None:
    value = ctx.get("__runner_gap_ms")
    if isinstance(value, (int, float)) and float(value) >= 0:
        return float(value)
    return None


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
    normalized_gap = (
        float(runner_gap_ms)
        if isinstance(runner_gap_ms, (int, float)) and runner_gap_ms >= 0
        else None
    )
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
    record: TraceRecord,
    previous_trace_exit: datetime | None,
) -> TraceRecord:
    route = record.route
    fallback_gap = route.runner_gap_ms if isinstance(route, RouteInfo) else None
    effective_gap = _resolve_prev_trace_gap_ms(
        previous_trace_exit=previous_trace_exit,
        current_enter=record.t_enter,
        fallback_gap_ms=fallback_gap,
    )
    if effective_gap is None:
        return record
    patched_route = RouteInfo(
        process_group=route.process_group if isinstance(route, RouteInfo) else None,
        handoff_from=route.handoff_from if isinstance(route, RouteInfo) else None,
        route_hop=route.route_hop if isinstance(route, RouteInfo) else None,
        parent_span_id=route.parent_span_id if isinstance(route, RouteInfo) else None,
        runner_gap_ms=effective_gap,
    )
    return TraceRecord(
        trace_id=record.trace_id,
        scenario=record.scenario,
        step_index=record.step_index,
        step_name=record.step_name,
        work_index=record.work_index,
        t_enter=record.t_enter,
        t_exit=record.t_exit,
        duration_ms=record.duration_ms,
        msg_in=record.msg_in,
        msg_out=record.msg_out,
        msg_out_count=record.msg_out_count,
        ctx_before=record.ctx_before,
        ctx_after=record.ctx_after,
        ctx_diff=record.ctx_diff,
        status=record.status,
        error=record.error,
        route=patched_route,
        span_id=record.span_id,
        parent_span_id=record.parent_span_id,
    )


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
