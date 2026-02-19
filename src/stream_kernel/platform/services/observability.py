from __future__ import annotations

import inspect
from dataclasses import dataclass
from dataclasses import field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.observers.observer import ExecutionObserver
from stream_kernel.platform.services.messaging.reply_coordinator import (
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent


@runtime_checkable
class ObservabilityService(Protocol):
    # Framework-level execution observability gateway.
    # Runner emits lifecycle events here; backend fan-out is implementation-defined.
    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        raise NotImplementedError("ObservabilityService.before_node must be implemented")

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
        raise NotImplementedError("ObservabilityService.after_node must be implemented")

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
        raise NotImplementedError("ObservabilityService.on_node_error must be implemented")

    def on_run_end(self) -> None:
        raise NotImplementedError("ObservabilityService.on_run_end must be implemented")


@runtime_checkable
class ObservabilityPipelineService(ObservabilityService, Protocol):
    # Unified service-level callback API for ingress/outbound/lifecycle observability.
    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.publish_trace must be implemented")

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.publish_log must be implemented")

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.publish_metric must be implemented")

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.publish_monitoring must be implemented")

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.on_ingress must be implemented")

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> None:
        raise NotImplementedError("ObservabilityPipelineService.on_terminal_event must be implemented")

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        raise NotImplementedError(
            "ObservabilityPipelineService.on_ingress_rate_limit_decision must be implemented"
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
        raise NotImplementedError(
            "ObservabilityPipelineService.on_outbound_policy_decision must be implemented"
        )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        raise NotImplementedError(
            "ObservabilityPipelineService.on_runtime_lifecycle_event must be implemented"
        )


@service(name="observability_service")
@dataclass(slots=True)
class NoOpObservabilityService(ObservabilityPipelineService):
    # Default platform observability implementation when no concrete observers are configured.
    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        _ = (node_name, payload, ctx, trace_id)
        return None

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
        _ = (node_name, payload, ctx, trace_id, outputs, state)
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
    ) -> None:
        _ = (node_name, payload, ctx, trace_id, error, state)
        return None

    def on_run_end(self) -> None:
        return None

    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        _ = (event, trace_id, attributes)
        return None

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        _ = (event, trace_id, attributes)
        return None

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        _ = (event, trace_id, attributes)
        return None

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        _ = (event, trace_id, attributes)
        return None

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        _ = (trace_id, reply_to)
        return None

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> None:
        _ = (trace_id, terminal_event)
        return None

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        _ = (trace_id, allowed, source_node, source_role, limiter_profile)
        return None

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
        _ = (stage, decision, trace_id, key, profile, attempt)
        return None

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        _ = (event, process_group, details)
        return None


@service(name="fanout_observability_service")
@dataclass(slots=True)
class FanoutObservabilityService(ObservabilityPipelineService):
    # Runtime fan-out service: forwards lifecycle events to discovered observers.
    observers: list[ExecutionObserver] = field(default_factory=list)

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        return [
            observer.before_node(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
            )
            for observer in self.observers
        ]

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
        states = state if isinstance(state, list) else [None] * len(self.observers)
        routed_outputs: list[object] = []
        for observer, observer_state in zip(self.observers, states, strict=False):
            produced = observer.after_node(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                outputs=outputs,
                state=observer_state,
            )
            routed_outputs.extend(_coerce_optional_outputs(produced))
        if not routed_outputs:
            return None
        return routed_outputs

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
        states = state if isinstance(state, list) else [None] * len(self.observers)
        routed_outputs: list[object] = []
        for observer, observer_state in zip(self.observers, states, strict=False):
            produced = observer.on_node_error(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                error=error,
                state=observer_state,
            )
            routed_outputs.extend(_coerce_optional_outputs(produced))
        if not routed_outputs:
            return None
        return routed_outputs

    def on_run_end(self) -> None:
        for observer in self.observers:
            observer.on_run_end()

    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._fanout_optional(
            "publish_trace",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )
        self._fanout_optional(
            "on_trace_event",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    async def publish_trace_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        attrs = dict(attributes or {})
        await self._fanout_optional_async(
            "publish_trace_async",
            fallback_method_name="publish_trace",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )
        await self._fanout_optional_async(
            "on_trace_event_async",
            fallback_method_name="on_trace_event",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._fanout_optional(
            "publish_log",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )
        self._fanout_optional(
            "on_log_event",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    async def publish_log_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        attrs = dict(attributes or {})
        await self._fanout_optional_async(
            "publish_log_async",
            fallback_method_name="publish_log",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )
        await self._fanout_optional_async(
            "on_log_event_async",
            fallback_method_name="on_log_event",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._fanout_optional(
            "publish_metric",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )
        self._fanout_optional(
            "on_metric_event",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    async def publish_metric_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        attrs = dict(attributes or {})
        await self._fanout_optional_async(
            "publish_metric_async",
            fallback_method_name="publish_metric",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )
        await self._fanout_optional_async(
            "on_metric_event_async",
            fallback_method_name="on_metric_event",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._fanout_optional(
            "publish_monitoring",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )
        self._fanout_optional(
            "on_monitoring_event",
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    async def publish_monitoring_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        attrs = dict(attributes or {})
        await self._fanout_optional_async(
            "publish_monitoring_async",
            fallback_method_name="publish_monitoring",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )
        await self._fanout_optional_async(
            "on_monitoring_event_async",
            fallback_method_name="on_monitoring_event",
            event=event,
            trace_id=trace_id,
            attributes=attrs,
        )

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        self._fanout_optional("on_ingress", trace_id=trace_id, reply_to=reply_to)

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> None:
        self._fanout_optional("on_terminal_event", trace_id=trace_id, terminal_event=terminal_event)

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        self._fanout_optional(
            "on_ingress_rate_limit_decision",
            trace_id=trace_id,
            allowed=allowed,
            source_node=source_node,
            source_role=source_role,
            limiter_profile=limiter_profile,
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
        self._fanout_optional(
            "on_outbound_policy_decision",
            stage=stage,
            decision=decision,
            trace_id=trace_id,
            key=key,
            profile=profile,
            attempt=attempt,
        )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        self._fanout_optional(
            "on_runtime_lifecycle_event",
            event=event,
            process_group=process_group,
            details=dict(details or {}),
        )

    def _fanout_optional(self, method_name: str, **kwargs: object) -> None:
        for observer in self.observers:
            callback = getattr(observer, method_name, None)
            if callable(callback):
                try:
                    callback(**kwargs)
                except Exception:
                    # Observability fan-out must not break business execution path
                    # when one optional stream/exporter callback fails.
                    continue

    async def _fanout_optional_async(
        self,
        method_name: str,
        *,
        fallback_method_name: str | None = None,
        **kwargs: object,
    ) -> None:
        for observer in self.observers:
            callback = getattr(observer, method_name, None)
            if not callable(callback) and isinstance(fallback_method_name, str):
                callback = getattr(observer, fallback_method_name, None)
            if not callable(callback):
                continue
            try:
                result = callback(**kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                continue


@service(name="reply_aware_observability_service")
@dataclass(slots=True)
class ReplyAwareObservabilityService(ObservabilityPipelineService):
    # Decorates base observability with correlated request/reply policy hooks.
    # inner=None so DI can auto-instantiate without args; _inner() degrades to NoOp gracefully.
    inner: object = None
    reply_coordinator: object = inject.service(ReplyCoordinatorService)

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        return self._inner().before_node(
            node_name=node_name,
            payload=payload,
            ctx=ctx,
            trace_id=trace_id,
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
        return self._inner().after_node(
            node_name=node_name,
            payload=payload,
            ctx=ctx,
            trace_id=trace_id,
            outputs=outputs,
            state=state,
        )

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
        return self._inner().on_node_error(
            node_name=node_name,
            payload=payload,
            ctx=ctx,
            trace_id=trace_id,
            error=error,
            state=state,
        )

    def on_run_end(self) -> None:
        self._inner().on_run_end()

    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._inner().publish_trace(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_trace_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self._inner(), "publish_trace_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self._inner().publish_trace(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._inner().publish_log(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_log_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self._inner(), "publish_log_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self._inner().publish_log(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._inner().publish_metric(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_metric_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self._inner(), "publish_metric_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self._inner().publish_metric(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self._inner().publish_monitoring(
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    async def publish_monitoring_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self._inner(), "publish_monitoring_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self._inner().publish_monitoring(
            event=event,
            trace_id=trace_id,
            attributes=dict(attributes or {}),
        )

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        self._inner().on_ingress(trace_id=trace_id, reply_to=reply_to)
        self._reply_coordinator().register_if_requested(
            trace_id=trace_id,
            reply_to=reply_to,
        )

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> None:
        self._inner().on_terminal_event(trace_id=trace_id, terminal_event=terminal_event)
        self._reply_coordinator().complete_if_waiting(
            trace_id=trace_id,
            terminal_event=terminal_event,
        )

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        self._inner().on_ingress_rate_limit_decision(
            trace_id=trace_id,
            allowed=allowed,
            source_node=source_node,
            source_role=source_role,
            limiter_profile=limiter_profile,
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
        self._inner().on_outbound_policy_decision(
            stage=stage,
            decision=decision,
            trace_id=trace_id,
            key=key,
            profile=profile,
            attempt=attempt,
        )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        self._inner().on_runtime_lifecycle_event(
            event=event,
            process_group=process_group,
            details=dict(details or {}),
        )

    def _inner(self) -> ObservabilityPipelineService:
        if isinstance(self.inner, ObservabilityPipelineService):
            return self.inner
        return resolve_pipeline_observability(self.inner)

    def _reply_coordinator(self) -> ReplyCoordinatorService:
        if isinstance(self.reply_coordinator, ReplyCoordinatorService):
            return self.reply_coordinator
        if (
            callable(getattr(self.reply_coordinator, "register_if_requested", None))
            and callable(getattr(self.reply_coordinator, "complete_if_waiting", None))
        ):
            return self.reply_coordinator  # type: ignore[return-value]
        raise ValueError(
            "ReplyAwareObservabilityService reply_coordinator is not resolved via DI"
        )


def legacy_reply_aware_observability(
    *,
    inner: object,
    reply_waiter: object,
    timeout_seconds: int = 30,
) -> ReplyAwareObservabilityService:
    # Transitional helper for tests/callers that still pass waiter directly to runner.
    service = ReplyAwareObservabilityService(inner=inner)
    service.reply_coordinator = legacy_reply_coordinator(
        reply_waiter=reply_waiter,
        timeout_seconds=timeout_seconds,
    )
    return service


@dataclass(slots=True)
class _PipelineObservabilityAdapter(ObservabilityPipelineService):
    inner: object

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        callback = getattr(self.inner, "before_node", None)
        if callable(callback):
            return callback(node_name=node_name, payload=payload, ctx=ctx, trace_id=trace_id)
        return None

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
        callback = getattr(self.inner, "after_node", None)
        if callable(callback):
            return callback(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                outputs=outputs,
                state=state,
            )
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
        callback = getattr(self.inner, "on_node_error", None)
        if callable(callback):
            return callback(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                error=error,
                state=state,
            )
        return None

    def on_run_end(self) -> None:
        callback = getattr(self.inner, "on_run_end", None)
        if callable(callback):
            callback()

    def publish_trace(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_trace", None)
        if callable(callback):
            callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            return
        fallback = getattr(self.inner, "on_trace_event", None)
        if callable(fallback):
            fallback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_trace_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_trace_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self.publish_trace(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_log(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_log", None)
        if callable(callback):
            callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            return
        fallback = getattr(self.inner, "on_log_event", None)
        if callable(fallback):
            fallback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_log_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_log_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self.publish_log(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_metric(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_metric", None)
        if callable(callback):
            callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            return
        fallback = getattr(self.inner, "on_metric_event", None)
        if callable(fallback):
            fallback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_metric_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_metric_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self.publish_metric(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def publish_monitoring(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_monitoring", None)
        if callable(callback):
            callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            return
        fallback = getattr(self.inner, "on_monitoring_event", None)
        if callable(fallback):
            fallback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    async def publish_monitoring_async(
        self,
        *,
        event: object,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        callback = getattr(self.inner, "publish_monitoring_async", None)
        if callable(callback):
            result = callback(event=event, trace_id=trace_id, attributes=dict(attributes or {}))
            if inspect.isawaitable(result):
                await result
            return
        self.publish_monitoring(event=event, trace_id=trace_id, attributes=dict(attributes or {}))

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        callback = getattr(self.inner, "on_ingress", None)
        if callable(callback):
            callback(trace_id=trace_id, reply_to=reply_to)

    def on_terminal_event(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> None:
        callback = getattr(self.inner, "on_terminal_event", None)
        if callable(callback):
            callback(trace_id=trace_id, terminal_event=terminal_event)

    def on_ingress_rate_limit_decision(
        self,
        *,
        trace_id: str | None,
        allowed: bool,
        source_node: str | None,
        source_role: str | None,
        limiter_profile: str | None,
    ) -> None:
        callback = getattr(self.inner, "on_ingress_rate_limit_decision", None)
        if callable(callback):
            callback(
                trace_id=trace_id,
                allowed=allowed,
                source_node=source_node,
                source_role=source_role,
                limiter_profile=limiter_profile,
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
        callback = getattr(self.inner, "on_outbound_policy_decision", None)
        if callable(callback):
            callback(
                stage=stage,
                decision=decision,
                trace_id=trace_id,
                key=key,
                profile=profile,
                attempt=attempt,
            )

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        callback = getattr(self.inner, "on_runtime_lifecycle_event", None)
        if callable(callback):
            callback(event=event, process_group=process_group, details=dict(details or {}))


def resolve_pipeline_observability(candidate: object | None) -> ObservabilityPipelineService:
    if isinstance(candidate, ObservabilityPipelineService):
        return candidate
    if (
        candidate is not None
        and callable(getattr(candidate, "before_node", None))
        and callable(getattr(candidate, "after_node", None))
        and callable(getattr(candidate, "on_node_error", None))
        and callable(getattr(candidate, "on_run_end", None))
    ):
        return _PipelineObservabilityAdapter(inner=candidate)
    return NoOpObservabilityService()


def coerce_pipeline_observability(candidate: object | None) -> ObservabilityPipelineService:
    if isinstance(candidate, ObservabilityPipelineService):
        return candidate
    if candidate is not None:
        return _PipelineObservabilityAdapter(inner=candidate)
    return NoOpObservabilityService()


def _coerce_optional_outputs(candidate: object) -> list[object]:
    if candidate is None:
        return []
    if isinstance(candidate, list):
        return [item for item in candidate if item is not None]
    return [candidate]
