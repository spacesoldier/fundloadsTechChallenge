from __future__ import annotations

import asyncio
from collections import deque
from threading import Condition
from time import monotonic

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

    def wait_for_item(self, timeout_seconds: float) -> bool:
        # Optional efficient wait hook used by long-running runner loops.
        _ = timeout_seconds
        return self.size() > 0

    async def wait_for_item_async(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        if self.size() > 0:
            return True
        if timeout == 0.0:
            return False
        deadline = monotonic() + timeout
        while True:
            if self.size() > 0:
                return True
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.001, max(0.0, remaining)))

    def close(self) -> None:
        # Optional shutdown hook; default no-op for transports without close semantics.
        return None

    def is_closed(self) -> bool:
        return False


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
        self._cv = Condition()
        self._closed = False
        self._async_waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[bool]]] = []

    def push(self, envelope: object) -> None:
        with self._cv:
            if self._closed:
                raise RuntimeError("InMemoryQueue is closed")
            self._queue.append(envelope)
            self._cv.notify()
            self._notify_async_waiters_unlocked(ready=True)

    def pop(self) -> object | None:
        with self._cv:
            if not self._queue:
                return None
            return self._queue.popleft()

    def size(self) -> int:
        with self._cv:
            return len(self._queue)

    def wait_for_item(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        with self._cv:
            if self._queue:
                return True
            if self._closed:
                return False
            if timeout == 0.0:
                return False
            deadline = monotonic() + timeout
            remaining = timeout
            while remaining > 0:
                self._cv.wait(remaining)
                if self._queue:
                    return True
                if self._closed:
                    return False
                remaining = deadline - monotonic()
            return False

    async def wait_for_item_async(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        loop = asyncio.get_running_loop()
        with self._cv:
            if self._queue:
                return True
            if self._closed:
                return False
            if timeout == 0.0:
                return False
            waiter: asyncio.Future[bool] = loop.create_future()
            self._async_waiters.append((loop, waiter))
        try:
            return bool(await asyncio.wait_for(waiter, timeout=timeout))
        except asyncio.TimeoutError:
            return self.size() > 0
        finally:
            with self._cv:
                self._async_waiters = [
                    (candidate_loop, candidate_waiter)
                    for candidate_loop, candidate_waiter in self._async_waiters
                    if candidate_waiter is not waiter
                ]

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
            self._notify_async_waiters_unlocked(ready=False)

    def is_closed(self) -> bool:
        with self._cv:
            return self._closed

    def _notify_async_waiters_unlocked(self, *, ready: bool) -> None:
        if not self._async_waiters:
            return
        waiters = list(self._async_waiters)
        self._async_waiters.clear()
        for loop, waiter in waiters:
            try:
                loop.call_soon_threadsafe(_resolve_waiter_future, waiter, ready)
            except Exception:
                continue


@service(name="execution_queue_tcp_local")
class TcpLocalQueue(QueuePort):
    # Placeholder queue contract for tcp_local runtime profile.
    # Real cross-process bridge is integrated in later phases.
    # Phase 2 baseline keeps deterministic local semantics with distinct profile type.
    def __init__(self, *, transport: SecureTcpTransport | None = None) -> None:
        self._queue: deque[object] = deque()
        self._transport = transport
        self._transport_rejects = 0
        self._cv = Condition()
        self._closed = False
        self._async_waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[bool]]] = []

    def push(self, envelope: object) -> None:
        with self._cv:
            if self._closed:
                raise RuntimeError("TcpLocalQueue is closed")
        if isinstance(envelope, (bytes, bytearray, memoryview)):
            self._push_framed(bytes(envelope))
            return
        with self._cv:
            self._queue.append(envelope)
            self._cv.notify()
            self._notify_async_waiters_unlocked(ready=True)

    def pop(self) -> object | None:
        with self._cv:
            if not self._queue:
                return None
            return self._queue.popleft()

    def size(self) -> int:
        with self._cv:
            return len(self._queue)

    def wait_for_item(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        with self._cv:
            if self._queue:
                return True
            if self._closed:
                return False
            if timeout == 0.0:
                return False
            deadline = monotonic() + timeout
            remaining = timeout
            while remaining > 0:
                self._cv.wait(remaining)
                if self._queue:
                    return True
                if self._closed:
                    return False
                remaining = deadline - monotonic()
            return False

    async def wait_for_item_async(self, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        loop = asyncio.get_running_loop()
        with self._cv:
            if self._queue:
                return True
            if self._closed:
                return False
            if timeout == 0.0:
                return False
            waiter: asyncio.Future[bool] = loop.create_future()
            self._async_waiters.append((loop, waiter))
        try:
            return bool(await asyncio.wait_for(waiter, timeout=timeout))
        except asyncio.TimeoutError:
            return self.size() > 0
        finally:
            with self._cv:
                self._async_waiters = [
                    (candidate_loop, candidate_waiter)
                    for candidate_loop, candidate_waiter in self._async_waiters
                    if candidate_waiter is not waiter
                ]

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
            self._notify_async_waiters_unlocked(ready=False)

    def is_closed(self) -> bool:
        with self._cv:
            return self._closed

    def transport_reject_count(self) -> int:
        # Diagnostic counter for rejected tcp-local boundary frames.
        return self._transport_rejects

    def _push_framed(self, framed: bytes) -> None:
        if self._transport is None:
            with self._cv:
                self._queue.append(framed)
                self._cv.notify()
                self._notify_async_waiters_unlocked(ready=True)
            return
        try:
            secure = self._transport.decode_framed_message(framed)
        except SecureTcpTransportError as exc:
            self._transport_rejects += 1
            raise ValueError("tcp_local transport reject: invalid frame") from exc
        with self._cv:
            self._queue.append(_secure_to_envelope(secure))
            self._cv.notify()
            self._notify_async_waiters_unlocked(ready=True)

    def _notify_async_waiters_unlocked(self, *, ready: bool) -> None:
        if not self._async_waiters:
            return
        waiters = list(self._async_waiters)
        self._async_waiters.clear()
        for loop, waiter in waiters:
            try:
                loop.call_soon_threadsafe(_resolve_waiter_future, waiter, ready)
            except Exception:
                continue


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


def _resolve_waiter_future(waiter: asyncio.Future[bool], ready: bool) -> None:
    if waiter.done():
        return
    waiter.set_result(bool(ready))
