from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.reply_waiter import ReplyWaiterService, TerminalEvent


@runtime_checkable
class ReplyCoordinatorService(Protocol):
    # Reply correlation policy boundary used by runner/lifecycle integration points.
    def register_if_requested(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
        timeout_seconds: int | None = None,
    ) -> bool:
        raise NotImplementedError(
            "ReplyCoordinatorService.register_if_requested must be implemented"
        )

    def complete_if_waiting(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> bool:
        raise NotImplementedError(
            "ReplyCoordinatorService.complete_if_waiting must be implemented"
        )


@service(name="reply_coordinator_service")
@dataclass(slots=True)
class InMemoryReplyCoordinatorService(ReplyCoordinatorService):
    # Thin coordinator over ReplyWaiterService: centralizes register/complete policy.
    reply_waiter: object = inject.service(ReplyWaiterService)
    default_timeout_seconds: int = 30

    def register_if_requested(
        self,
        *,
        trace_id: str | None,
        reply_to: str | None,
        timeout_seconds: int | None = None,
    ) -> bool:
        if not isinstance(trace_id, str) or not trace_id:
            return False
        if not isinstance(reply_to, str) or not reply_to:
            return False
        waiter = self._waiter()
        if waiter is None:
            return False
        resolved_timeout = timeout_seconds or self.default_timeout_seconds
        if not isinstance(resolved_timeout, int) or resolved_timeout <= 0:
            return False
        waiter.register(
            trace_id=trace_id,
            reply_to=reply_to,
            timeout_seconds=resolved_timeout,
        )
        return True

    def complete_if_waiting(
        self,
        *,
        trace_id: str | None,
        terminal_event: TerminalEvent | None,
    ) -> bool:
        if not isinstance(trace_id, str) or not trace_id:
            return False
        if not isinstance(terminal_event, TerminalEvent):
            return False
        waiter = self._waiter()
        if waiter is None:
            return False
        return waiter.complete(trace_id=trace_id, event=terminal_event)

    def _waiter(self) -> ReplyWaiterService | None:
        if isinstance(self.reply_waiter, ReplyWaiterService):
            return self.reply_waiter
        if (
            callable(getattr(self.reply_waiter, "register", None))
            and callable(getattr(self.reply_waiter, "complete", None))
            and callable(getattr(self.reply_waiter, "cancel", None))
            and callable(getattr(self.reply_waiter, "expire", None))
            and callable(getattr(self.reply_waiter, "poll", None))
            and callable(getattr(self.reply_waiter, "in_flight", None))
        ):
            return self.reply_waiter  # type: ignore[return-value]
        return None


def legacy_reply_coordinator(
    *,
    reply_waiter: object,
    timeout_seconds: int = 30,
) -> ReplyCoordinatorService:
    # Transitional builder: keep old tests/callers passing waiter directly to runner.
    coordinator = InMemoryReplyCoordinatorService(default_timeout_seconds=timeout_seconds)
    coordinator.reply_waiter = reply_waiter
    return coordinator

