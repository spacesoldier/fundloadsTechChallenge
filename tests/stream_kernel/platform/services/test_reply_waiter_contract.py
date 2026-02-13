from __future__ import annotations

from stream_kernel.platform.services.reply_waiter import (
    InMemoryReplyWaiterService,
    TerminalEvent,
)


def test_reply_waiter_register_complete_roundtrip() -> None:
    # REPLY-01: request roundtrip returns terminal event to original waiter.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    trace_id = "t-1"
    service.register(trace_id=trace_id, reply_to="http:req-1", timeout_seconds=30)

    completed = service.complete(
        trace_id=trace_id,
        event=TerminalEvent(status="success", payload={"ok": True}),
    )
    assert completed is True
    assert service.poll(trace_id=trace_id) == TerminalEvent(status="success", payload={"ok": True})
    assert service.in_flight() == 0


def test_reply_waiter_timeout_cleans_registry() -> None:
    # REPLY-02: timeout should clean waiter and expose deterministic timeout terminal.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    trace_id = "t-timeout"
    service.register(trace_id=trace_id, reply_to="http:req-timeout", timeout_seconds=1)

    expired = service.expire(now_epoch_seconds=2)
    assert expired == [trace_id]
    assert service.poll(trace_id=trace_id) == TerminalEvent(status="timeout", error="reply_timeout")
    assert service.in_flight() == 0


def test_reply_waiter_duplicate_terminal_is_ignored() -> None:
    # REPLY-03: duplicate terminal event must not produce duplicate completion.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    trace_id = "t-dup"
    service.register(trace_id=trace_id, reply_to="http:req-dup", timeout_seconds=30)

    first = service.complete(
        trace_id=trace_id,
        event=TerminalEvent(status="success", payload={"n": 1}),
    )
    second = service.complete(
        trace_id=trace_id,
        event=TerminalEvent(status="success", payload={"n": 2}),
    )
    assert first is True
    assert second is False
    assert service.poll(trace_id=trace_id) == TerminalEvent(status="success", payload={"n": 1})


def test_reply_waiter_cancel_has_deterministic_terminal() -> None:
    # REPLY-04: explicit cancellation should resolve waiter deterministically.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    trace_id = "t-cancel"
    service.register(trace_id=trace_id, reply_to="ws:req-cancel", timeout_seconds=30)

    cancelled = service.cancel(trace_id=trace_id, reason="client_disconnect")
    assert cancelled is True
    assert service.poll(trace_id=trace_id) == TerminalEvent(status="cancelled", error="client_disconnect")
    assert service.in_flight() == 0


def test_reply_waiter_late_terminal_after_cancel_is_dropped() -> None:
    # REPLY-05: late terminal event after cancellation/timeout must be dropped.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    trace_id = "t-late"
    service.register(trace_id=trace_id, reply_to="http:req-late", timeout_seconds=30)
    assert service.cancel(trace_id=trace_id, reason="client_disconnect") is True

    completed = service.complete(
        trace_id=trace_id,
        event=TerminalEvent(status="success", payload={"late": True}),
    )
    assert completed is False
    assert service.poll(trace_id=trace_id) == TerminalEvent(status="cancelled", error="client_disconnect")


def test_reply_waiter_step_e_counters_track_timeout_cancel_and_late_drop() -> None:
    # REPLY-06: instrumentation counters should reflect timeout/cancel/late-drop lifecycle.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    service.register(trace_id="t-cancel", reply_to="http:req-cancel", timeout_seconds=30)
    service.register(trace_id="t-timeout", reply_to="http:req-timeout", timeout_seconds=1)
    service.register(trace_id="t-complete", reply_to="http:req-complete", timeout_seconds=30)

    assert service.cancel(trace_id="t-cancel", reason="client_closed") is True
    assert service.expire(now_epoch_seconds=2) == ["t-timeout"]
    assert service.complete(
        trace_id="t-complete",
        event=TerminalEvent(status="success", payload={"ok": True}),
    )
    assert service.complete(
        trace_id="t-complete",
        event=TerminalEvent(status="success", payload={"ok": False}),
    ) is False
    assert service.complete(
        trace_id="t-late",
        event=TerminalEvent(status="error", error="late"),
    ) is False

    counters = service.diagnostics_counters()
    assert counters["registered"] == 3
    assert counters["cancelled"] == 1
    assert counters["expired"] == 1
    assert counters["completed"] == 1
    assert counters["duplicate_terminal"] == 1
    assert counters["late_reply_drop"] == 1
    assert counters["in_flight"] == 0


def test_reply_waiter_step_e_diagnostic_events_do_not_leak_secret_values() -> None:
    # REPLY-07: diagnostics must be sanitized and not expose raw reason/payload/error strings.
    service = InMemoryReplyWaiterService(now_fn=lambda: 0)
    secret = "sk_live_abc123"
    service.register(trace_id="t1", reply_to="http:req", timeout_seconds=30)
    assert service.cancel(trace_id="t1", reason=secret) is True
    assert service.complete(
        trace_id="t-late",
        event=TerminalEvent(status="error", payload={"secret": secret}, error=secret),
    ) is False

    events = service.diagnostic_events()
    serialized = repr(events)
    assert secret not in serialized
    assert any(event["kind"] == "cancelled" for event in events)
    assert any(event["kind"] == "late_reply_drop" for event in events)
