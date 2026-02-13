from __future__ import annotations

from stream_kernel.platform.services.reply_coordinator import (
    InMemoryReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.reply_waiter import (
    InMemoryReplyWaiterService,
    TerminalEvent,
)


def test_reply_coordinator_registers_waiter_on_reply_to() -> None:
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
    coordinator = legacy_reply_coordinator(reply_waiter=waiter, timeout_seconds=15)

    registered = coordinator.register_if_requested(
        trace_id="t1",
        reply_to="http:req-1",
    )

    assert registered is True
    assert waiter.in_flight() == 1


def test_reply_coordinator_completes_terminal_event() -> None:
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
    waiter.register(trace_id="t1", reply_to="http:req-1", timeout_seconds=30)
    coordinator = legacy_reply_coordinator(reply_waiter=waiter)

    completed = coordinator.complete_if_waiting(
        trace_id="t1",
        terminal_event=TerminalEvent(status="success", payload={"ok": True}),
    )

    assert completed is True
    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"ok": True})


def test_reply_coordinator_is_noop_without_valid_waiter_contract() -> None:
    coordinator = legacy_reply_coordinator(reply_waiter=object())

    assert coordinator.register_if_requested(trace_id="t1", reply_to="http:req-1") is False
    assert (
        coordinator.complete_if_waiting(
            trace_id="t1",
            terminal_event=TerminalEvent(status="success"),
        )
        is False
    )


def test_reply_coordinator_ignores_empty_reply_inputs() -> None:
    coordinator = InMemoryReplyCoordinatorService(reply_waiter=InMemoryReplyWaiterService(now_fn=lambda: 0))

    assert coordinator.register_if_requested(trace_id="", reply_to="http:req-1") is False
    assert coordinator.register_if_requested(trace_id="t1", reply_to="") is False
    assert coordinator.complete_if_waiting(trace_id="", terminal_event=TerminalEvent(status="success")) is False
