"""
IPC-BG-00x: background reader thread for PipeExecutionIpcTransportAdapter.

All tests in this file are RED before the refactor and GREEN after.
"""
from __future__ import annotations

import time

import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcEndpointRegistry,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    _PipeReceiveBuffer,
    _drain_pipe,
)


class _EndpointRegistry(ExecutionIpcEndpointRegistry):
    def __init__(self) -> None:
        self._store = InMemoryKvStore()

    def get(self, key: str) -> object | None:
        return self._store.get(key)

    def set(self, key: str, value: object) -> None:
        self._store.set(key, value)

    def delete(self, key: str) -> None:
        self._store.delete(key)


# ---------------------------------------------------------------------------
# IPC-BG-001: allocate_endpoints does NOT start a reader (send-side only)
# ---------------------------------------------------------------------------

def test_bg_reader_thread_not_started_on_allocate_endpoints() -> None:
    """allocate_endpoints() is the send-side path; must NOT register a reader.
    Starting a reader here would race with leaf control ingress that reads the
    same pipe directly.
    """
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    _parent_ep, _child_ep = adapter.allocate_endpoints("worker#1")

    time.sleep(0.05)
    assert "worker#1" not in adapter._reader_targets
    assert adapter._loop_thread is None

    adapter.close()


def test_bg_reader_thread_starts_on_attach_endpoint() -> None:
    """attach_endpoint() must also register the target and start the reader."""
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#2")
    receiver.attach_endpoint(child_ep)

    time.sleep(0.05)
    assert receiver._loop_thread is not None
    assert receiver._loop_thread.is_alive()
    assert child_ep.target_id in receiver._reader_targets

    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# IPC-BG-002: message is drained into buffer by reader BEFORE recv() is called
# ---------------------------------------------------------------------------

def test_bg_reader_drains_message_into_buffer_proactively() -> None:
    """
    A message sent via the sender is drained into receiver's buffer by the
    background reader thread. recv(timeout=0.0) must find it without doing
    any direct pipe drain itself.
    """
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#3")
    receiver.attach_endpoint(child_ep)

    # Send from sender side
    sender.send("worker#3", {"msg": "hello-bg"})

    # Wait for background reader to drain
    time.sleep(0.05)

    # recv with timeout=0.0 — non-blocking; data should already be in buffer
    msg = receiver.recv(child_ep.target_id, timeout=0.0)
    assert msg is not None, "message must be in buffer after background drain"
    assert msg.payload == {"msg": "hello-bg"}

    sender.close()
    receiver.close()


def test_bg_reader_notifies_data_available_callback_on_drain() -> None:
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#3-callback")
    receiver.attach_endpoint(child_ep)
    notified: list[int] = []

    def _on_data_available() -> None:
        notified.append(1)

    assert receiver.register_data_available_callback(
        child_ep.target_id,
        _on_data_available,
    )

    sender.send("worker#3-callback", {"msg": "hello-callback"})
    time.sleep(0.05)

    assert len(notified) >= 1
    msg = receiver.recv(child_ep.target_id, timeout=0.0)
    assert msg is not None
    assert msg.payload == {"msg": "hello-callback"}

    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# IPC-BG-003: recv() returns quickly when message is available (not full timeout)
# ---------------------------------------------------------------------------

def test_recv_returns_fast_when_message_already_in_buffer() -> None:
    """
    recv(timeout=0.1) must return in well under 10ms when data is already
    in the buffer (drained by background reader). Before the fix it would
    block for poll_interval_seconds before noticing data.
    """
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#4")
    receiver.attach_endpoint(child_ep)

    sender.send("worker#4", {"fast": True})
    time.sleep(0.05)  # let reader drain into buffer

    t0 = time.monotonic()
    msg = receiver.recv(child_ep.target_id, timeout=0.1)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert msg is not None
    assert elapsed_ms < 5.0, (
        f"recv() should return in < 5ms when buffer already has data, "
        f"got {elapsed_ms:.1f}ms"
    )

    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# IPC-BG-004: message arriving mid-wait wakes recv() promptly
# ---------------------------------------------------------------------------

def test_recv_woken_promptly_when_message_arrives_mid_wait() -> None:
    """
    recv(timeout=0.2) must return well before the full timeout when the
    background reader enqueues a message mid-wait.
    """
    import threading

    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#5")
    receiver.attach_endpoint(child_ep)
    time.sleep(0.02)  # let reader thread start

    result: list[float] = []

    def _send_after_delay() -> None:
        time.sleep(0.03)  # 30ms delay
        sender.send("worker#5", {"mid": "wake"})

    t = threading.Thread(target=_send_after_delay, daemon=True)
    t.start()

    t0 = time.monotonic()
    msg = receiver.recv(child_ep.target_id, timeout=0.2)
    elapsed_ms = (time.monotonic() - t0) * 1000
    t.join()

    assert msg is not None, "recv() must return the message, not timeout"
    # Should wake within ~30ms + one reader poll cycle (5ms) + margin
    assert elapsed_ms < 80.0, (
        f"recv() must wake promptly after message arrives, got {elapsed_ms:.1f}ms"
    )

    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# IPC-BG-005: multiple messages drained correctly — no duplication, no loss
# ---------------------------------------------------------------------------

def test_bg_reader_drains_multiple_messages_in_order() -> None:
    """All messages sent must arrive exactly once, in order."""
    N = 10
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#6")
    receiver.attach_endpoint(child_ep)

    for i in range(N):
        sender.send("worker#6", {"seq": i})

    time.sleep(0.1)  # let reader drain all

    received = []
    for _ in range(N):
        msg = receiver.recv(child_ep.target_id, timeout=0.0)
        if msg is None:
            break
        received.append(msg.payload["seq"])

    assert received == list(range(N)), f"expected ordered sequence, got {received}"

    sender.close()
    receiver.close()


# ---------------------------------------------------------------------------
# IPC-BG-006: close() stops the reader thread cleanly
# ---------------------------------------------------------------------------

def test_close_stops_reader_thread() -> None:
    """close() must stop the background reader loop thread."""
    sender = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    receiver = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)

    _parent_ep, child_ep = sender.allocate_endpoints("worker#7")
    receiver.attach_endpoint(child_ep)
    time.sleep(0.05)

    loop_thread = receiver._loop_thread
    assert loop_thread is not None and loop_thread.is_alive()

    sender.close()
    receiver.close()
    time.sleep(0.05)

    assert not loop_thread.is_alive(), "reader loop must stop after close()"


# ---------------------------------------------------------------------------
# IPC-BG-007: adapter without allocate_endpoints uses direct drain (no reader)
# ---------------------------------------------------------------------------

def test_recv_falls_back_to_direct_drain_when_no_reader_registered() -> None:
    """
    An adapter where allocate_endpoints / attach_endpoint was never called
    still works via the direct drain fallback path.
    """
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    # No endpoints registered → _reader_targets is empty → no reader thread
    assert len(adapter._reader_targets) == 0
    assert adapter._loop_thread is None
    adapter.close()


def test_drain_pipe_limits_items_per_pass() -> None:
    class _StubEndpoint:
        def __init__(self, total: int) -> None:
            self._remaining = total

        def recv(self, *, timeout: float | None = None) -> object | None:
            _ = timeout
            if self._remaining <= 0:
                return None
            self._remaining -= 1
            return {"seq": self._remaining}

    endpoint = _StubEndpoint(total=1000)
    buffer = _PipeReceiveBuffer()
    drained = _drain_pipe(endpoint, buffer, max_items_per_pass=128)
    assert drained == 128
    # Remaining payload must still be available for subsequent passes.
    drained_next = _drain_pipe(endpoint, buffer, max_items_per_pass=128)
    assert drained_next == 128
