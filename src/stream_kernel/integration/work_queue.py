from __future__ import annotations

from collections import deque

from stream_kernel.application_context.service import service


class QueuePort:
    # Port for message transport (Execution runtime and routing integration §3.1).
    def push(self, envelope: object) -> None:
        raise NotImplementedError("QueuePort.push must be implemented")

    def pop(self) -> object | None:
        raise NotImplementedError("QueuePort.pop must be implemented")

    def size(self) -> int:
        raise NotImplementedError("QueuePort.size must be implemented")


class TopicPort:
    # Port for pub/sub-like message streams.
    def publish(self, message: object) -> None:
        raise NotImplementedError("TopicPort.publish must be implemented")

    def consume(self) -> object | None:
        raise NotImplementedError("TopicPort.consume must be implemented")

    def size(self) -> int:
        raise NotImplementedError("TopicPort.size must be implemented")


@service(name="execution_queue")
class InMemoryQueue(QueuePort):
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


@service(name="execution_topic")
class InMemoryTopic(TopicPort):
    # In-memory topic-like adapter for bootstrap and local tests.
    # This is a minimal single-subscriber contract; multi-subscriber fan-out is delegated to runtime/router.
    def __init__(self) -> None:
        self._messages: deque[object] = deque()

    def publish(self, message: object) -> None:
        self._messages.append(message)

    def consume(self) -> object | None:
        if not self._messages:
            return None
        return self._messages.popleft()

    def size(self) -> int:
        return len(self._messages)
