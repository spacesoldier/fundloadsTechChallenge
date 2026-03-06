from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable
from typing import Literal, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

_WAITERS_INFLIGHT_KEY = "reply_waiter.inflight"
_WAITERS_TERMINAL_KEY = "reply_waiter.terminal"
_WAITERS_COUNTERS_KEY = "reply_waiter.counters"
_WAITERS_EVENTS_KEY = "reply_waiter.events"

_DEFAULT_COUNTERS: dict[str, int] = {
    "registered": 0,
    "completed": 0,
    "cancelled": 0,
    "expired": 0,
    "duplicate_terminal": 0,
    "late_reply_drop": 0,
}
_VALID_TERMINAL_STATUS: set[str] = {"success", "error", "cancelled", "timeout"}

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


class ReplyWaiterRegistryStore(KVStore):
    # KV marker contract for correlated reply waiter state.
    pass


@service(name="reply_waiter_service")
class InMemoryReplyWaiterService(ReplyWaiterService):
    # KV-backed correlated waiter registry for request/reply terminal delivery.
    store: object = inject.kv(ReplyWaiterRegistryStore)

    def __init__(
        self,
        *,
        store: KVStore | None = None,
        now_fn: Callable[[], int] | None = None,
        max_diagnostic_events: int = 256,
    ) -> None:
        self._explicit_store = store if isinstance(store, KVStore) else None
        self._fallback_store = InMemoryKvStore()
        self._now = now_fn or (lambda: int(time.time()))
        self._max_diagnostic_events = max(16, int(max_diagnostic_events))
        self._ensure_state()

    def register(self, *, trace_id: str, reply_to: str, timeout_seconds: int) -> None:
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("trace_id must be a non-empty string")
        if not isinstance(reply_to, str) or not reply_to:
            raise ValueError("reply_to must be a non-empty string")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        inflight = self._inflight()
        if trace_id in inflight:
            raise ValueError(f"waiter already registered for trace_id '{trace_id}'")
        deadline = int(self._now()) + timeout_seconds
        inflight[trace_id] = _WaiterState(
            reply_to=reply_to,
            deadline_epoch_seconds=deadline,
        )
        # New registration replaces stale terminal state for the same correlation key.
        terminal = self._terminal()
        terminal.pop(trace_id, None)
        self._set_inflight(inflight)
        self._set_terminal(terminal)
        self._increment("registered")
        self._record_event(kind="registered", trace_id=trace_id)

    def complete(self, *, trace_id: str, event: TerminalEvent) -> bool:
        inflight = self._inflight()
        terminal = self._terminal()
        if trace_id not in inflight:
            if trace_id in terminal:
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
        if trace_id in terminal:
            self._increment("duplicate_terminal")
            self._record_event(
                kind="duplicate_terminal",
                trace_id=trace_id,
                terminal_status=event.status,
            )
            return False
        inflight.pop(trace_id, None)
        terminal[trace_id] = event
        self._set_inflight(inflight)
        self._set_terminal(terminal)
        self._increment("completed")
        self._record_event(
            kind="completed",
            trace_id=trace_id,
            terminal_status=event.status,
        )
        return True

    def cancel(self, *, trace_id: str, reason: str | None = None) -> bool:
        inflight = self._inflight()
        terminal = self._terminal()
        if trace_id not in inflight:
            if trace_id in terminal:
                self._increment("duplicate_terminal")
                self._record_event(kind="duplicate_terminal", trace_id=trace_id, terminal_status="cancelled")
            else:
                self._increment("late_reply_drop")
                self._record_event(kind="late_reply_drop", trace_id=trace_id, terminal_status="cancelled")
            return False
        inflight.pop(trace_id, None)
        terminal[trace_id] = TerminalEvent(
            status="cancelled",
            error=reason or "cancelled",
        )
        self._set_inflight(inflight)
        self._set_terminal(terminal)
        self._increment("cancelled")
        # Diagnostics intentionally do not include raw `reason` values to avoid secret leakage.
        self._record_event(kind="cancelled", trace_id=trace_id, terminal_status="cancelled")
        return True

    def expire(self, *, now_epoch_seconds: int) -> list[str]:
        inflight = self._inflight()
        terminal = self._terminal()
        expired: list[str] = []
        for trace_id, state in list(inflight.items()):
            if now_epoch_seconds < state.deadline_epoch_seconds:
                continue
            inflight.pop(trace_id, None)
            terminal[trace_id] = TerminalEvent(status="timeout", error="reply_timeout")
            expired.append(trace_id)
            self._increment("expired")
            self._record_event(kind="expired", trace_id=trace_id, terminal_status="timeout")
        self._set_inflight(inflight)
        self._set_terminal(terminal)
        return expired

    def poll(self, *, trace_id: str) -> TerminalEvent | None:
        return self._terminal().get(trace_id)

    def in_flight(self) -> int:
        return len(self._inflight())

    def diagnostics_counters(self) -> dict[str, int]:
        # Sanitized operational counters for observability/reporting.
        counters = self._counters()
        return {
            **counters,
            "in_flight": len(self._inflight()),
        }

    def diagnostic_events(self) -> list[dict[str, object]]:
        # Sanitized event stream: no raw payload/error/reason/reply_to values.
        return [dict(item) for item in self._events()]

    def _increment(self, key: str) -> None:
        counters = self._counters()
        counters[key] = counters.get(key, 0) + 1
        self._set_counters(counters)

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
        events = self._events()
        events.append(event)
        if len(events) > self._max_diagnostic_events:
            events = events[-self._max_diagnostic_events :]
        self._set_events(events)

    def _store(self) -> KVStore:
        if isinstance(self._explicit_store, KVStore):
            return self._explicit_store
        candidate = self.store
        if isinstance(candidate, KVStore):
            return candidate
        return self._fallback_store

    def _ensure_state(self) -> None:
        if not isinstance(self._store().get(_WAITERS_INFLIGHT_KEY), dict):
            self._store().set(_WAITERS_INFLIGHT_KEY, {})
        if not isinstance(self._store().get(_WAITERS_TERMINAL_KEY), dict):
            self._store().set(_WAITERS_TERMINAL_KEY, {})
        if not isinstance(self._store().get(_WAITERS_COUNTERS_KEY), dict):
            self._store().set(_WAITERS_COUNTERS_KEY, dict(_DEFAULT_COUNTERS))
        if not isinstance(self._store().get(_WAITERS_EVENTS_KEY), list):
            self._store().set(_WAITERS_EVENTS_KEY, [])

    def _inflight(self) -> dict[str, _WaiterState]:
        raw = self._store().get(_WAITERS_INFLIGHT_KEY)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, _WaiterState] = {}
        for trace_id, item in raw.items():
            if not isinstance(trace_id, str) or not isinstance(item, dict):
                continue
            reply_to = item.get("reply_to")
            deadline = item.get("deadline_epoch_seconds")
            if not isinstance(reply_to, str) or not isinstance(deadline, int):
                continue
            result[trace_id] = _WaiterState(reply_to=reply_to, deadline_epoch_seconds=deadline)
        return result

    def _set_inflight(self, inflight: dict[str, _WaiterState]) -> None:
        payload = {
            trace_id: {
                "reply_to": state.reply_to,
                "deadline_epoch_seconds": int(state.deadline_epoch_seconds),
            }
            for trace_id, state in inflight.items()
            if isinstance(trace_id, str) and isinstance(state, _WaiterState)
        }
        self._store().set(_WAITERS_INFLIGHT_KEY, payload)

    def _terminal(self) -> dict[str, TerminalEvent]:
        raw = self._store().get(_WAITERS_TERMINAL_KEY)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, TerminalEvent] = {}
        for trace_id, item in raw.items():
            if not isinstance(trace_id, str) or not isinstance(item, dict):
                continue
            status = item.get("status")
            if not isinstance(status, str) or status not in _VALID_TERMINAL_STATUS:
                continue
            result[trace_id] = TerminalEvent(
                status=status,  # type: ignore[arg-type]
                payload=item.get("payload"),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
            )
        return result

    def _set_terminal(self, terminal: dict[str, TerminalEvent]) -> None:
        payload = {
            trace_id: {
                "status": event.status,
                "payload": event.payload,
                "error": event.error,
            }
            for trace_id, event in terminal.items()
            if isinstance(trace_id, str) and isinstance(event, TerminalEvent)
        }
        self._store().set(_WAITERS_TERMINAL_KEY, payload)

    def _counters(self) -> dict[str, int]:
        raw = self._store().get(_WAITERS_COUNTERS_KEY)
        counters = dict(_DEFAULT_COUNTERS)
        if not isinstance(raw, dict):
            return counters
        for key in counters:
            value = raw.get(key)
            if isinstance(value, int):
                counters[key] = value
        return counters

    def _set_counters(self, counters: dict[str, int]) -> None:
        payload = {key: int(value) for key, value in counters.items() if isinstance(key, str)}
        self._store().set(_WAITERS_COUNTERS_KEY, payload)

    def _events(self) -> list[dict[str, object]]:
        raw = self._store().get(_WAITERS_EVENTS_KEY)
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]

    def _set_events(self, events: list[dict[str, object]]) -> None:
        sanitized: list[dict[str, object]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            sanitized.append(dict(item))
        self._store().set(_WAITERS_EVENTS_KEY, sanitized)


# Backward-compatible alias used by Step-A RED tests and transition docs.
PendingReplyWaiterService = InMemoryReplyWaiterService
