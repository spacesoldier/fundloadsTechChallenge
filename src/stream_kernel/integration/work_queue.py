from __future__ import annotations

from collections import deque


class WorkQueue:
    # Port for message transport (Execution runtime and routing integration §3.1).
    def push(self, envelope: object) -> None:
        raise NotImplementedError("WorkQueue.push must be implemented")

    def pop(self) -> object | None:
        raise NotImplementedError("WorkQueue.pop must be implemented")

    def size(self) -> int:
        raise NotImplementedError("WorkQueue.size must be implemented")


class InMemoryWorkQueue(WorkQueue):
    # In-memory FIFO queue for deterministic runs (Execution runtime and routing integration §8.1).
    def __init__(self) -> None:
        self._queue: deque[object] = deque()

    def push(self, envelope: object) -> None:
        self._queue.append(envelope)

    def pop(self) -> object | None:
        if not self._queue:
            return None
        return self._queue.popleft()

    def size(self) -> int:
        return len(self._queue)
