from __future__ import annotations

# QueuePort behavior is specified in docs/framework/initial_stage/Execution runtime and routing integration.md.
import threading
import time

from stream_kernel.integration.work_queue import InMemoryQueue


def test_work_queue_fifo_order() -> None:
    # FIFO ordering is required for deterministic runs.
    queue = InMemoryQueue()
    queue.push("A")
    queue.push("B")
    assert queue.pop() == "A"
    assert queue.pop() == "B"


def test_work_queue_empty_pop_returns_none() -> None:
    # Empty pop should return None (non-blocking default).
    queue = InMemoryQueue()
    assert queue.pop() is None


def test_work_queue_push_after_empty() -> None:
    # Queue should continue working after empty pops.
    queue = InMemoryQueue()
    assert queue.pop() is None
    queue.push("A")
    assert queue.pop() == "A"


def test_work_queue_fifo_under_mixed_producers() -> None:
    # Interleaved pushes must preserve FIFO order.
    queue = InMemoryQueue()
    queue.push("A")
    queue.push("B")
    queue.push("C")
    assert queue.pop() == "A"
    assert queue.pop() == "B"
    assert queue.pop() == "C"


def test_work_queue_size_tracks_items() -> None:
    # size should reflect queue length.
    queue = InMemoryQueue()
    assert queue.size() == 0
    queue.push("A")
    queue.push("B")
    assert queue.size() == 2
    queue.pop()
    assert queue.size() == 1


def test_work_queue_wait_for_item_times_out_when_queue_is_empty() -> None:
    queue = InMemoryQueue()

    started = time.monotonic()
    ready = queue.wait_for_item(0.05)
    elapsed = time.monotonic() - started

    assert ready is False
    assert elapsed >= 0.03


def test_work_queue_wait_for_item_unblocks_when_item_is_pushed() -> None:
    queue = InMemoryQueue()
    result: list[bool] = []

    def _waiter() -> None:
        result.append(queue.wait_for_item(0.5))

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()
    time.sleep(0.02)
    queue.push("A")
    thread.join(timeout=1.0)

    assert result == [True]
    assert queue.pop() == "A"


def test_work_queue_close_wakes_waiters_and_reports_closed() -> None:
    queue = InMemoryQueue()
    result: list[bool] = []

    def _waiter() -> None:
        result.append(queue.wait_for_item(0.5))

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()
    time.sleep(0.02)
    queue.close()
    thread.join(timeout=1.0)

    assert queue.is_closed() is True
    assert result == [False]
