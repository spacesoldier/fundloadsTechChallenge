from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable
from typing import Literal, Protocol, runtime_checkable

from stream_kernel.application_context.service import service

TerminalStatus = Literal["success", "error", "cancelled", "timeout"]


@dataclass(frozen=True, slots=True)
class TerminalEvent:
    # Deterministic terminal outcome for correlated request/reply flows.
    status: TerminalStatus
    payload: object | None = None
    error: str | None = None


@runtime_checkable
class ReplyWaiterService(Protocol):
    # Correlated waiter contract used by web/execution boundary adapters.
    def register(self, *, trace_id: str, reply_to: str, timeout_seconds: int) -> None:
        raise NotImplementedError("ReplyWaiterService.register must be implemented")

    def complete(self, *, trace_id: str, event: TerminalEvent) -> bool:
        # Return True only when waiter was completed by this call.
        raise NotImplementedError("ReplyWaiterService.complete must be implemented")

    def cancel(self, *, trace_id: str, reason: str | None = None) -> bool:
        # Return True only when waiter was cancelled by this call.
        raise NotImplementedError("ReplyWaiterService.cancel must be implemented")

    def expire(self, *, now_epoch_seconds: int) -> list[str]:
        # Remove timed-out waiters and return their trace ids.
        raise NotImplementedError("ReplyWaiterService.expire must be implemented")

    def poll(self, *, trace_id: str) -> TerminalEvent | None:
        # Read terminal outcome by trace_id; None means no terminal event.
        raise NotImplementedError("ReplyWaiterService.poll must be implemented")

    def in_flight(self) -> int:
        raise NotImplementedError("ReplyWaiterService.in_flight must be implemented")


@dataclass(slots=True)
class _WaiterState:
    reply_to: str
    deadline_epoch_seconds: int


@service(name="reply_waiter_service")
class InMemoryReplyWaiterService(ReplyWaiterService):
    # In-memory correlated waiter registry for request/reply terminal delivery.
    def __init__(
        self,
        *,
        now_fn: Callable[[], int] | None = None,
        max_diagnostic_events: int = 256,
    ) -> None:
        self._now = now_fn or (lambda: int(time.time()))
        self._inflight: dict[str, _WaiterState] = {}
        self._terminal: dict[str, TerminalEvent] = {}
        self._max_diagnostic_events = max(16, int(max_diagnostic_events))
        self._counters: dict[str, int] = {
            "registered": 0,
            "completed": 0,
            "cancelled": 0,
            "expired": 0,
            "duplicate_terminal": 0,
            "late_reply_drop": 0,
        }
        self._events: list[dict[str, object]] = []

    def register(self, *, trace_id: str, reply_to: str, timeout_seconds: int) -> None:
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("trace_id must be a non-empty string")
        if not isinstance(reply_to, str) or not reply_to:
            raise ValueError("reply_to must be a non-empty string")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if trace_id in self._inflight:
            raise ValueError(f"waiter already registered for trace_id '{trace_id}'")
        deadline = int(self._now()) + timeout_seconds
        self._inflight[trace_id] = _WaiterState(
            reply_to=reply_to,
            deadline_epoch_seconds=deadline,
        )
        # New registration replaces stale terminal state for the same correlation key.
        self._terminal.pop(trace_id, None)
        self._increment("registered")
        self._record_event(kind="registered", trace_id=trace_id)

    def complete(self, *, trace_id: str, event: TerminalEvent) -> bool:
        if trace_id not in self._inflight:
            if trace_id in self._terminal:
                self._increment("duplicate_terminal")
                self._record_event(
                    kind="duplicate_terminal",
                    trace_id=trace_id,
                    terminal_status=event.status,
                )
            else:
                self._increment("late_reply_drop")
                self._record_event(
                    kind="late_reply_drop",
                    trace_id=trace_id,
                    terminal_status=event.status,
                )
            return False
        if trace_id in self._terminal:
            self._increment("duplicate_terminal")
            self._record_event(
                kind="duplicate_terminal",
                trace_id=trace_id,
                terminal_status=event.status,
            )
            return False
        self._inflight.pop(trace_id, None)
        self._terminal[trace_id] = event
        self._increment("completed")
        self._record_event(
            kind="completed",
            trace_id=trace_id,
            terminal_status=event.status,
        )
        return True

    def cancel(self, *, trace_id: str, reason: str | None = None) -> bool:
        if trace_id not in self._inflight:
            if trace_id in self._terminal:
                self._increment("duplicate_terminal")
                self._record_event(kind="duplicate_terminal", trace_id=trace_id, terminal_status="cancelled")
            else:
                self._increment("late_reply_drop")
                self._record_event(kind="late_reply_drop", trace_id=trace_id, terminal_status="cancelled")
            return False
        self._inflight.pop(trace_id, None)
        self._terminal[trace_id] = TerminalEvent(
            status="cancelled",
            error=reason or "cancelled",
        )
        self._increment("cancelled")
        # Diagnostics intentionally do not include raw `reason` values to avoid secret leakage.
        self._record_event(kind="cancelled", trace_id=trace_id, terminal_status="cancelled")
        return True

    def expire(self, *, now_epoch_seconds: int) -> list[str]:
        expired: list[str] = []
        for trace_id, state in list(self._inflight.items()):
            if now_epoch_seconds < state.deadline_epoch_seconds:
                continue
            self._inflight.pop(trace_id, None)
            self._terminal[trace_id] = TerminalEvent(status="timeout", error="reply_timeout")
            expired.append(trace_id)
            self._increment("expired")
            self._record_event(kind="expired", trace_id=trace_id, terminal_status="timeout")
        return expired

    def poll(self, *, trace_id: str) -> TerminalEvent | None:
        return self._terminal.get(trace_id)

    def in_flight(self) -> int:
        return len(self._inflight)

    def diagnostics_counters(self) -> dict[str, int]:
        # Sanitized operational counters for observability/reporting.
        return {
            **self._counters,
            "in_flight": len(self._inflight),
        }

    def diagnostic_events(self) -> list[dict[str, object]]:
        # Sanitized event stream: no raw payload/error/reason/reply_to values.
        return [dict(item) for item in self._events]

    def _increment(self, key: str) -> None:
        self._counters[key] = self._counters.get(key, 0) + 1

    def _record_event(
        self,
        *,
        kind: str,
        trace_id: str,
        terminal_status: TerminalStatus | None = None,
    ) -> None:
        event: dict[str, object] = {
            "kind": kind,
            "trace_id": trace_id,
            "ts_epoch_seconds": int(self._now()),
        }
        if terminal_status is not None:
            event["terminal_status"] = terminal_status
        self._events.append(event)
        if len(self._events) > self._max_diagnostic_events:
            self._events = self._events[-self._max_diagnostic_events :]


# Backward-compatible alias used by Step-A RED tests and transition docs.
PendingReplyWaiterService = InMemoryReplyWaiterService
