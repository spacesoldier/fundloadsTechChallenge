from __future__ import annotations

# WorkQueue behavior is specified in docs/framework/initial_stage/Execution runtime and routing integration.md.
from stream_kernel.integration.work_queue import InMemoryWorkQueue


def test_work_queue_fifo_order() -> None:
    # FIFO ordering is required for deterministic runs.
    queue = InMemoryWorkQueue()
    queue.push("A")
    queue.push("B")
    assert queue.pop() == "A"
    assert queue.pop() == "B"


def test_work_queue_empty_pop_returns_none() -> None:
    # Empty pop should return None (non-blocking default).
    queue = InMemoryWorkQueue()
    assert queue.pop() is None


def test_work_queue_push_after_empty() -> None:
    # Queue should continue working after empty pops.
    queue = InMemoryWorkQueue()
    assert queue.pop() is None
    queue.push("A")
    assert queue.pop() == "A"


def test_work_queue_fifo_under_mixed_producers() -> None:
    # Interleaved pushes must preserve FIFO order.
    queue = InMemoryWorkQueue()
    queue.push("A")
    queue.push("B")
    queue.push("C")
    assert queue.pop() == "A"
    assert queue.pop() == "B"
    assert queue.pop() == "C"


def test_work_queue_size_tracks_items() -> None:
    # size should reflect queue length.
    queue = InMemoryWorkQueue()
    assert queue.size() == 0
    queue.push("A")
    queue.push("B")
    assert queue.size() == 2
    queue.pop()
    assert queue.size() == 1
