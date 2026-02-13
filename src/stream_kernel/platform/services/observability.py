from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.observers.observer import ExecutionObserver
from stream_kernel.platform.services.reply_coordinator import (
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.reply_waiter import TerminalEvent


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


@service(name="observability_service")
@dataclass(slots=True)
class NoOpObservabilityService(ObservabilityService):
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


@dataclass(slots=True)
class FanoutObservabilityService(ObservabilityService):
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
    ) -> None:
        states = state if isinstance(state, list) else [None] * len(self.observers)
        for observer, observer_state in zip(self.observers, states, strict=False):
            observer.after_node(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                outputs=outputs,
                state=observer_state,
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
    ) -> None:
        states = state if isinstance(state, list) else [None] * len(self.observers)
        for observer, observer_state in zip(self.observers, states, strict=False):
            observer.on_node_error(
                node_name=node_name,
                payload=payload,
                ctx=ctx,
                trace_id=trace_id,
                error=error,
                state=observer_state,
            )

    def on_run_end(self) -> None:
        for observer in self.observers:
            observer.on_run_end()


@dataclass(slots=True)
class ReplyAwareObservabilityService(ObservabilityService):
    # Decorates base observability with correlated request/reply policy hooks.
    inner: object
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
    ) -> None:
        self._inner().after_node(
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
    ) -> None:
        self._inner().on_node_error(
            node_name=node_name,
            payload=payload,
            ctx=ctx,
            trace_id=trace_id,
            error=error,
            state=state,
        )

    def on_run_end(self) -> None:
        self._inner().on_run_end()

    def on_ingress(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
    ) -> None:
        on_ingress = getattr(self._inner(), "on_ingress", None)
        if callable(on_ingress):
            on_ingress(trace_id=trace_id, reply_to=reply_to)
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
        on_terminal_event = getattr(self._inner(), "on_terminal_event", None)
        if callable(on_terminal_event):
            on_terminal_event(trace_id=trace_id, terminal_event=terminal_event)
        self._reply_coordinator().complete_if_waiting(
            trace_id=trace_id,
            terminal_event=terminal_event,
        )

    def _inner(self) -> ObservabilityService:
        if isinstance(self.inner, ObservabilityService):
            return self.inner
        if (
            callable(getattr(self.inner, "before_node", None))
            and callable(getattr(self.inner, "after_node", None))
            and callable(getattr(self.inner, "on_node_error", None))
            and callable(getattr(self.inner, "on_run_end", None))
        ):
            return self.inner  # type: ignore[return-value]
        raise ValueError("ReplyAwareObservabilityService inner is not a valid ObservabilityService")

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
