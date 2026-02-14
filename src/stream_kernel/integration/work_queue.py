from __future__ import annotations

from collections import deque

from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.secure_tcp_transport import (
    SecureEnvelope,
    SecureTcpTransport,
    SecureTcpTransportError,
)
from stream_kernel.routing.envelope import Envelope


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


@service(name="execution_queue_tcp_local")
class TcpLocalQueue(QueuePort):
    # Placeholder queue contract for tcp_local runtime profile.
    # Real cross-process bridge is integrated in later phases.
    # Phase 2 baseline keeps deterministic local semantics with distinct profile type.
    def __init__(self, *, transport: SecureTcpTransport | None = None) -> None:
        self._queue: deque[object] = deque()
        self._transport = transport
        self._transport_rejects = 0

    def push(self, envelope: object) -> None:
        if isinstance(envelope, (bytes, bytearray, memoryview)):
            self._push_framed(bytes(envelope))
            return
        self._queue.append(envelope)

    def pop(self) -> object | None:
        if not self._queue:
            return None
        return self._queue.popleft()

    def size(self) -> int:
        return len(self._queue)

    def transport_reject_count(self) -> int:
        # Diagnostic counter for rejected tcp-local boundary frames.
        return self._transport_rejects

    def _push_framed(self, framed: bytes) -> None:
        if self._transport is None:
            self._queue.append(framed)
            return
        try:
            secure = self._transport.decode_framed_message(framed)
        except SecureTcpTransportError as exc:
            self._transport_rejects += 1
            raise ValueError("tcp_local transport reject: invalid frame") from exc
        self._queue.append(_secure_to_envelope(secure))


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


@service(name="execution_topic_tcp_local")
class TcpLocalTopic(TopicPort):
    # Placeholder topic contract for tcp_local runtime profile.
    # Real cross-process bridge is integrated in later phases.
    # Phase 2 baseline keeps deterministic local semantics with distinct profile type.
    def __init__(self, *, transport: SecureTcpTransport | None = None) -> None:
        self._messages: deque[object] = deque()
        self._transport = transport
        self._transport_rejects = 0

    def publish(self, message: object) -> None:
        if isinstance(message, (bytes, bytearray, memoryview)):
            self._publish_framed(bytes(message))
            return
        self._messages.append(message)

    def consume(self) -> object | None:
        if not self._messages:
            return None
        return self._messages.popleft()

    def size(self) -> int:
        return len(self._messages)

    def transport_reject_count(self) -> int:
        # Diagnostic counter for rejected tcp-local boundary frames.
        return self._transport_rejects

    def _publish_framed(self, framed: bytes) -> None:
        if self._transport is None:
            self._messages.append(framed)
            return
        try:
            secure = self._transport.decode_framed_message(framed)
        except SecureTcpTransportError as exc:
            self._transport_rejects += 1
            raise ValueError("tcp_local transport reject: invalid frame") from exc
        self._messages.append(_secure_to_envelope(secure))


def _secure_to_envelope(secure: SecureEnvelope) -> Envelope:
    return Envelope(
        payload=secure.payload_bytes,
        trace_id=secure.trace_id,
        target=secure.target,
        reply_to=secure.reply_to,
        span_id=secure.span_id,
    )
