from __future__ import annotations

import atexit
import multiprocessing as mp
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Condition, Event, Lock, Thread

from stream_kernel.adapters.contracts import AdapterBatch, adapter
from stream_kernel.execution.transport.ipc.ipc_codec import (
    ExecutionIpcCodec,
    ExecutionIpcCodecError,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcAck,
    ExecutionIpcControlSignal,
    ExecutionIpcEndpointRegistry,
    ExecutionIpcKvStreamPort,
    ExecutionIpcMessage,
    ExecutionIpcPort,
    ExecutionIpcReceivePolicy,
    compose_execution_ipc_worker_target_id,
    decompose_execution_ipc_worker_target_id,
)
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

# Registry key used for lightweight metadata about endpoints and buffer policy.
# The actual pipe endpoints live in ExecutionIpcEndpointRegistry (KV port), not here.
_REGISTRY_KEY = "ipc.endpoint_registry"
_BUFFER_PREFIX = "ipc.buffer."
_SEND_RETRY_WAIT = Event()


class InMemoryExecutionIpcTransportAdapter(ExecutionIpcKvStreamPort):
    # Pure in-memory transport: used in tests and local single-process mode.
    # It emulates the IPC port contract with per-target receive buffers.
    def __init__(self, *, kv_store: KVStore | None = None) -> None:
        self._kv_store = kv_store if isinstance(kv_store, KVStore) else InMemoryKvStore()
        self._lock = Lock()
        self._ack_handlers: dict[str, Callable[[object], None]] = {}
        self._ack_enabled = False

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort:
        # DI hook: build_port is called by inject.ipc(..., receive_policy=...)
        # to register a receive-side buffer for the given target_id.
        # Normal runtime code does not call build_port directly.
        if isinstance(receive_policy, ExecutionIpcReceivePolicy):
            if not isinstance(target_id, str) or not target_id:
                raise ValueError("ExecutionIpcPort receive_policy requires a target_id")
            self._ensure_receiver(target_id, receive_policy)
        return ExecutionIpcPort(service=self, target_id=target_id)

    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> ExecutionIpcAck | None:
        # Expand adapter-level batches into individual messages and enqueue them.
        # In the in-memory adapter, the per-target buffer *is* the channel.
        # send() produces into that queue, recv() consumes from it.
        items = _expand_batch_payload(payload)
        enqueued = 0
        for item in items:
            message = ExecutionIpcMessage(
                target_id=target_id,
                payload=item,
                ts_epoch_ms=int(time.time() * 1000),
            )
            buffer = self._ensure_receiver(target_id, ExecutionIpcReceivePolicy(buffer_enabled=True))
            buffer.enqueue(message)
            enqueued += 1
        if self._ack_enabled:
            handler = self._ack_handlers.get(target_id)
            if handler is not None:
                handler(enqueued)
        if no_reply:
            return None
        return ExecutionIpcAck(status="accepted", enqueued=enqueued)

    def recv(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        # Read from the per-target receive buffer.
        # There is no background poller here: for the in-memory adapter, send()
        # is the producer and recv() is the consumer of the same deque.
        buffer = self._ensure_receiver(target_id, ExecutionIpcReceivePolicy(buffer_enabled=True))
        return buffer.recv(timeout=timeout)

    def recv_buffered(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        # In-memory transport has no separate channel drain step; recv() is already buffered-only.
        return self.recv(target_id, timeout=timeout)

    def metrics(self, target_id: str) -> dict[str, object]:
        # Simple queue depth/age diagnostics for observability.
        buffer = self._ensure_receiver(target_id, ExecutionIpcReceivePolicy(buffer_enabled=True))
        return buffer.metrics()

    def register_ack_handler(self, target_id: str, handler: Callable[[object], None]) -> None:
        if not callable(handler):
            raise ValueError("register_ack_handler requires a callable")
        self._ack_handlers[target_id] = handler

    def enable_ack(self, enabled: bool = True) -> None:
        self._ack_enabled = bool(enabled)

    def _ensure_receiver(
        self,
        target_id: str,
        policy: ExecutionIpcReceivePolicy,
    ) -> _IpcReceiveBuffer:
        # Create the per-target receive buffer if it does not exist yet.
        # This is where receive-side buffering policy is applied.
        #
        # The lock is needed because send() and recv() can race to create
        # the buffer on first access. We want exactly one buffer per target_id.
        # The buffer is stored via KVStore so we don't keep a private dict.
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionIpcKvStreamPort target_id must be a non-empty string")
        effective_policy = policy
        if not policy.buffer_enabled:
            # Unbuffered receive policy is still modeled with a buffer,
            # but with single-item semantics.
            effective_policy = ExecutionIpcReceivePolicy(
                buffer_enabled=False,
                batch_max_items=1,
                flush_interval_ms=0,
            )
        with self._lock:
            buffer_key = f"{_BUFFER_PREFIX}{target_id}"
            candidate = self._kv_store.get(buffer_key)
            if isinstance(candidate, _IpcReceiveBuffer):
                return candidate
            buffer = _IpcReceiveBuffer(effective_policy)
            self._kv_store.set(buffer_key, buffer)
            self._register_endpoint(target_id, effective_policy)
            return buffer

    def _register_endpoint(self, target_id: str, policy: ExecutionIpcReceivePolicy) -> None:
        # Store buffer policy metadata in the registry for diagnostics only.
        registry = self._kv_store.get(_REGISTRY_KEY)
        if not isinstance(registry, dict):
            registry = {}
        registry[target_id] = {
            "buffer_enabled": bool(policy.buffer_enabled),
            "batch_max_items": int(policy.batch_max_items),
            "flush_interval_ms": int(policy.flush_interval_ms),
        }
        self._kv_store.set(_REGISTRY_KEY, registry)


class PipeExecutionIpcTransportAdapter(ExecutionIpcKvStreamPort):
    # Pipe-backed adapter for cross-process IPC.
    # It does not own process lifecycle; it only sends/receives via pipe endpoints.
    # Payloads are drained into per-target buffers by a background asyncio loop.
    # The loop can use add_reader (edge-triggered) or timer-based polling depending
    # on poll_mode and platform support.
    def __init__(
        self,
        *,
        codec: str | ExecutionIpcCodec = "pickle",
        kv_store: KVStore | None = None,
        endpoint_registry: KVStore | None = None,
        context: mp.context.BaseContext | None = None,
        poll_interval_seconds: float = 0.005,
        poll_mode: str = "reader",
    ) -> None:
        self._kv_store = kv_store if isinstance(kv_store, KVStore) else InMemoryKvStore()
        self._endpoint_registry = (
            endpoint_registry if isinstance(endpoint_registry, KVStore) else InMemoryKvStore()
        )
        self._codec = codec if isinstance(codec, ExecutionIpcCodec) else ExecutionIpcCodec(codec)
        self._ctx = context or mp.get_context("spawn")
        self._endpoints: dict[str, _PipeEndpoint] = {}
        self._lock = Lock()
        self._loop: object | None = None
        self._loop_thread: object | None = None
        self._reader_targets: set[str] = set()
        self._reader_last_read: dict[str, float] = {}
        self._poll_interval_seconds = max(0.001, float(poll_interval_seconds))
        self._poll_mode = _normalize_poll_mode(poll_mode)
        self._close_join_timeout_seconds = 0.1
        self._stopping = Event()
        self._ack_handlers: dict[str, Callable[[object], None]] = {}
        self._ack_enabled = False
        self._sender_buffers: dict[str, _PipeSendBuffer] = {}
        self._sender_threads: dict[str, Thread] = {}
        self._writer_last_send: dict[str, float] = {}
        self._send_retry_backoff_seconds = 0.005
        atexit.register(self.close)

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort:
        # Pipe adapter ignores receive_policy; buffering is handled by the service.
        # build_port still exists to satisfy the port interface used by DI.
        _ = receive_policy
        return ExecutionIpcPort(service=self, target_id=target_id)

    def allocate_endpoints(self, target_id: str) -> tuple["_PipeEndpoint", "_PipeEndpoint"]:
        # Creates a duplex pipe and wraps both ends in _PipeEndpoint for send/recv.
        # The parent endpoint is kept locally; the child endpoint is handed to the child process.
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("PipeExecutionIpcTransportAdapter target_id must be a non-empty string")
        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        parent = _PipeEndpoint(target_id=target_id, connection=parent_conn, codec=self._codec)
        child = _PipeEndpoint(target_id=target_id, connection=child_conn, codec=self._codec)
        with self._lock:
            self._endpoints[target_id] = parent
            self._register_endpoint(target_id)
        return parent, child

    def attach_endpoint(self, endpoint: object, *, target_id: str | None = None) -> None:
        # Accepts either a _PipeEndpoint or a raw connection and registers it locally.
        resolved = self._coerce_endpoint(endpoint, target_id=target_id)
        if resolved is None:
            raise ValueError("PipeExecutionIpcTransportAdapter.attach_endpoint requires a pipe endpoint")
        with self._lock:
            self._endpoints[resolved.target_id] = resolved
            self._register_endpoint(resolved.target_id)

    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> ExecutionIpcAck | None:
        # Resolve endpoint and enqueue for background sender.
        # This keeps caller-side path non-blocking when OS pipe buffers are full.
        _ = self._resolve_endpoint(target_id)
        send_buffer = self._ensure_send_buffer(target_id)
        send_buffer.raise_if_error()
        items = _expand_batch_payload(payload)
        enqueued = 0
        for item in items:
            # Preserve fail-fast contract for bytes codec: unsupported payloads
            # must raise from send() instead of surfacing later in async sender.
            if self._codec.mode == "bytes":
                self._codec.encode(item)
            send_buffer.enqueue(item)
            enqueued += 1
        if no_reply:
            return None
        return ExecutionIpcAck(status="accepted", enqueued=enqueued)

    def recv(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        # Single receive path: non-blocking pipe drain -> receive buffer -> consumer.
        endpoint = self._resolve_endpoint(target_id)
        buffer = self._ensure_receive_buffer(target_id, endpoint)
        payload = self._recv_from_pipe_buffer(
            target_id=target_id,
            endpoint=endpoint,
            buffer=buffer,
            timeout=timeout,
        )
        if payload is None:
            return None
        return ExecutionIpcMessage(
            target_id=target_id,
            payload=payload,
            ts_epoch_ms=int(time.time() * 1000),
        )

    def recv_buffered(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        # Buffered receive uses the same single receive path as recv().
        endpoint = self._resolve_endpoint(target_id)
        buffer = self._ensure_receive_buffer(target_id, endpoint)
        payload = self._recv_from_pipe_buffer(
            target_id=target_id,
            endpoint=endpoint,
            buffer=buffer,
            timeout=timeout,
        )
        if payload is None:
            return None
        return ExecutionIpcMessage(
            target_id=target_id,
            payload=payload,
            ts_epoch_ms=int(time.time() * 1000),
        )

    def metrics(self, target_id: str) -> dict[str, object]:
        # Buffer metrics reflect how much data is waiting to be consumed/sent.
        buffer = self._get_buffer(target_id)
        send_buffer = self._get_send_buffer(target_id)
        outbound = (
            send_buffer.metrics()
            if send_buffer is not None
            else {"outbound_queue_depth": 0, "outbound_oldest_age_ms": 0}
        )
        if buffer is None:
            return {"queue_depth": 0, "oldest_age_ms": 0, **outbound}
        payload = buffer.metrics()
        payload.update(outbound)
        return payload

    def register_ack_handler(self, target_id: str, handler: Callable[[object], None]) -> None:
        if not callable(handler):
            raise ValueError("register_ack_handler requires a callable")
        self._ack_handlers[target_id] = handler

    def enable_ack(self, enabled: bool = True) -> None:
        self._ack_enabled = bool(enabled)

    def _resolve_endpoint(self, target_id: str) -> "_PipeEndpoint":
        # Local registry lookup, then lazy attach via endpoint registry if present.
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("PipeExecutionIpcTransportAdapter target_id must be a non-empty string")
        with self._lock:
            endpoint = self._endpoints.get(target_id)
        if endpoint is None:
            endpoint = self._resolve_endpoint_from_registry(target_id)
        if endpoint is None:
            raise ConnectionError(f"ipc transport endpoint not registered for target '{target_id}'")
        return endpoint

    def _resolve_endpoint_from_registry(self, target_id: str) -> "_PipeEndpoint" | None:
        # Endpoint registry may store raw connection objects.
        # Those are created by the lifecycle service during spawn and saved in KV.
        # Coerce them into _PipeEndpoint so we can use the codec abstraction.
        endpoint = self._endpoint_registry.get(target_id)
        resolved = self._coerce_endpoint(endpoint, target_id=target_id)
        if resolved is None:
            return None
        with self._lock:
            self._endpoints[target_id] = resolved
        return resolved

    def _register_endpoint(self, target_id: str) -> None:
        # Store only codec metadata; the actual endpoint object is kept in the KV registry.
        registry = self._kv_store.get(_REGISTRY_KEY)
        if not isinstance(registry, dict):
            registry = {}
        registry[target_id] = {"codec": self._codec.mode}
        self._kv_store.set(_REGISTRY_KEY, registry)

    def _ensure_send_buffer(self, target_id: str) -> "_PipeSendBuffer":
        with self._lock:
            buffer = self._sender_buffers.get(target_id)
            if buffer is None:
                buffer = _PipeSendBuffer()
                self._sender_buffers[target_id] = buffer
            thread = self._sender_threads.get(target_id)
            if thread is None or not thread.is_alive():
                thread = Thread(
                    target=self._sender_loop,
                    args=(target_id, buffer),
                    daemon=True,
                    name=f"ipc-pipe-send-{target_id}",
                )
                self._sender_threads[target_id] = thread
                thread.start()
        return buffer

    def _get_send_buffer(self, target_id: str) -> "_PipeSendBuffer" | None:
        with self._lock:
            return self._sender_buffers.get(target_id)

    def _sender_loop(self, target_id: str, buffer: "_PipeSendBuffer") -> None:
        while not self._stopping.is_set():
            payload = buffer.pop(timeout=0.05)
            if payload is None:
                if buffer.is_closed():
                    break
                continue
            while not self._stopping.is_set():
                try:
                    endpoint = self._resolve_endpoint(target_id)
                    endpoint.send(payload)
                    buffer.clear_error()
                    self._touch_writer(target_id)
                    break
                except ConnectionError:
                    # Endpoint availability can be transient during lifecycle transitions.
                    # Keep retrying without poisoning sender health for callers.
                    buffer.requeue_left(payload)
                    _SEND_RETRY_WAIT.wait(max(0.0, float(self._send_retry_backoff_seconds)))
                    break
                except Exception as exc:
                    buffer.set_error(exc)
                    # Requeue payload and retry asynchronously; send() caller stays non-blocking.
                    buffer.requeue_left(payload)
                    _SEND_RETRY_WAIT.wait(max(0.0, float(self._send_retry_backoff_seconds)))
                    break

    def _coerce_endpoint(self, endpoint: object, *, target_id: str | None) -> "_PipeEndpoint" | None:
        # Accept both already-wrapped endpoints and raw pipe connections.
        if isinstance(endpoint, _PipeEndpoint):
            return endpoint
        if _is_pipe_connection(endpoint) and isinstance(target_id, str) and target_id:
            return _PipeEndpoint(target_id=target_id, connection=endpoint, codec=self._codec)
        return None

    def _ensure_receive_buffer(self, target_id: str, endpoint: "_PipeEndpoint") -> "_PipeReceiveBuffer":
        # Each target_id gets a receive buffer.
        # Pipe draining is performed explicitly from recv/recv_buffered.
        _ = endpoint
        with self._lock:
            buffer = self._get_buffer(target_id)
            if buffer is None:
                buffer = _PipeReceiveBuffer()
                self._set_buffer(target_id, buffer)
        return buffer

    def _recv_from_pipe_buffer(
        self,
        target_id: str,
        endpoint: "_PipeEndpoint",
        buffer: "_PipeReceiveBuffer",
        timeout: float | None,
    ) -> object | None:
        ack_handler = lambda signal: self._handle_ack(target_id, signal)
        ack_enabled = self._ack_enabled and _target_uses_flow_control_ack(target_id)
        send_ack = lambda count: self._send_ack_for_target(
            target_id=target_id,
            endpoint=endpoint,
            count=count,
        )
        _drain_pipe(
            endpoint,
            buffer,
            on_read=lambda: self._touch_reader(target_id),
            on_ack=ack_handler,
            send_ack=send_ack,
            ack_enabled=ack_enabled,
        )
        payload = buffer.recv(timeout=0.0)
        if payload is None and timeout is not None and timeout > 0:
            deadline = _deadline(timeout)
            while payload is None:
                remaining = _remaining(deadline)
                if remaining <= 0:
                    break
                _drain_pipe(
                    endpoint,
                    buffer,
                    on_read=lambda: self._touch_reader(target_id),
                    on_ack=ack_handler,
                    send_ack=send_ack,
                    ack_enabled=ack_enabled,
                )
                payload = buffer.recv(timeout=min(remaining, self._poll_interval_seconds))
        return payload

    def _get_buffer(self, target_id: str) -> "_PipeReceiveBuffer" | None:
        key = f"{_BUFFER_PREFIX}{target_id}"
        candidate = self._kv_store.get(key)
        if isinstance(candidate, _PipeReceiveBuffer):
            return candidate
        return None

    def _set_buffer(self, target_id: str, buffer: "_PipeReceiveBuffer") -> None:
        key = f"{_BUFFER_PREFIX}{target_id}"
        self._kv_store.set(key, buffer)

    def close(self) -> None:
        # Graceful shutdown for sender workers.
        with self._lock:
            sender_buffers = list(self._sender_buffers.values())
            sender_threads = list(self._sender_threads.values())
        # Close outbound buffers first and give sender threads a chance to
        # drain queued payloads before we stop adapter loops.
        for buffer in sender_buffers:
            buffer.close()
        for sender in sender_threads:
            sender.join(timeout=max(0.01, float(self._close_join_timeout_seconds)))
        self._stopping.set()
        with self._lock:
            self._reader_last_read.clear()
            self._sender_threads.clear()
            self._sender_buffers.clear()
            self._writer_last_send.clear()

    def configure_polling(
        self,
        *,
        poll_mode: str | None = None,
        poll_interval_ms: float | int | None = None,
    ) -> None:
        # Runtime-level override for recv() drain cadence.
        with self._lock:
            if poll_mode is not None:
                self._poll_mode = _normalize_poll_mode(poll_mode)
            if poll_interval_ms is not None:
                if isinstance(poll_interval_ms, (int, float)) and poll_interval_ms > 0:
                    self._poll_interval_seconds = max(0.001, float(poll_interval_ms) / 1000.0)

    def _touch_reader(self, target_id: str) -> None:
        with self._lock:
            self._reader_last_read[target_id] = time.monotonic()

    def _touch_writer(self, target_id: str) -> None:
        with self._lock:
            self._writer_last_send[target_id] = time.monotonic()

    def _handle_ack(self, target_id: str, signal: object) -> None:
        handler = self._ack_handlers.get(target_id)
        if handler is None:
            return
        if isinstance(signal, ExecutionIpcControlSignal) and signal.target_id is None:
            handler(int(signal.count))
            return
        handler(signal)

    def _send_ack_for_target(
        self,
        *,
        target_id: str,
        endpoint: "_PipeEndpoint",
        count: int,
    ) -> None:
        ack_target_id = _flow_control_ack_target_id(target_id)
        ack_endpoint: _PipeEndpoint | None = None
        try:
            if ack_target_id == target_id:
                ack_endpoint = endpoint
            else:
                ack_endpoint = self._resolve_endpoint(ack_target_id)
        except Exception:
            ack_endpoint = None
        if ack_endpoint is None:
            return
        _send_ack(endpoint=ack_endpoint, count=count, source_target_id=target_id)


@adapter(
    name="execution_ipc_inmemory",
    kind="ipc.transport.inmemory",
    consumes=[],
    emits=[],
    binds=[("kv_stream", ExecutionIpcKvStreamPort)],
    execution_mode="async",
)
def execution_ipc_inmemory_adapter(settings: dict[str, object]) -> InMemoryExecutionIpcTransportAdapter:
    # Adapter factory for in-memory IPC transport.
    _ = settings
    return InMemoryExecutionIpcTransportAdapter()


@adapter(
    name="execution_ipc_pipe",
    kind="ipc.transport.pipe",
    consumes=[],
    emits=[],
    binds=[("kv_stream", ExecutionIpcKvStreamPort)],
    execution_mode="async",
)
def execution_ipc_pipe_adapter(settings: dict[str, object]) -> PipeExecutionIpcTransportAdapter:
    # Adapter factory for pipe-based IPC transport.
    codec = settings.get("codec", "pickle")
    if not isinstance(codec, str) or not codec:
        codec = "pickle"
    endpoint_registry = settings.get("endpoint_registry")
    registry = endpoint_registry if isinstance(endpoint_registry, KVStore) else None
    return PipeExecutionIpcTransportAdapter(
        codec=codec,
        endpoint_registry=registry,
    )


@dataclass(frozen=True, slots=True)
class _PipeEndpoint:
    # Thin wrapper around a single pipe endpoint.
    # It applies encoding/decoding and shields higher-level code from the raw pipe API.
    target_id: str
    connection: object
    codec: ExecutionIpcCodec
    _recv_lock: Lock = field(default_factory=Lock, compare=False, repr=False)

    def send(self, payload: object) -> None:
        # Prefer send_bytes when available so we can pass framed bytes directly.
        send_bytes = getattr(self.connection, "send_bytes", None)
        if callable(send_bytes):
            send_bytes(self.codec.encode(payload))
            return
        send = getattr(self.connection, "send", None)
        if callable(send):
            # When codec=bytes, we still push encoded bytes even if send() is available.
            if self.codec.mode == "bytes":
                send(self.codec.encode(payload))
                return
            send(payload)
            return
        raise ConnectionError("ipc pipe endpoint send is unavailable")

    def recv(self, *, timeout: float | None = None) -> object | None:
        # Poll-based receive with optional timeout to avoid blocking the caller.
        with self._recv_lock:
            poll = getattr(self.connection, "poll", None)
            if not callable(poll):
                raise ConnectionError("ipc pipe endpoint poll is unavailable")
            remaining = timeout
            if remaining is None:
                remaining = 0.0
            if remaining > 0 and not poll(remaining):
                return None
            if remaining <= 0 and not poll(0.0):
                return None
            recv_bytes = getattr(self.connection, "recv_bytes", None)
            if callable(recv_bytes):
                try:
                    payload = recv_bytes()
                except (EOFError, BrokenPipeError, OSError):
                    return None
                return self._decode_payload(payload)
            recv = getattr(self.connection, "recv", None)
            if callable(recv):
                try:
                    payload = recv()
                except (EOFError, BrokenPipeError, OSError):
                    return None
                return self._decode_payload(payload)
            raise ConnectionError("ipc pipe endpoint recv is unavailable")

    def recv_ready(self) -> object:
        # Read a payload assuming the pipe is already readable.
        with self._recv_lock:
            recv_bytes = getattr(self.connection, "recv_bytes", None)
            if callable(recv_bytes):
                try:
                    payload = recv_bytes()
                except (EOFError, BrokenPipeError, OSError):
                    return None
                return self._decode_payload(payload)
            recv = getattr(self.connection, "recv", None)
            if callable(recv):
                try:
                    payload = recv()
                except (EOFError, BrokenPipeError, OSError):
                    return None
                return self._decode_payload(payload)
            raise ConnectionError("ipc pipe endpoint recv is unavailable")

    def _decode_payload(self, payload: object) -> object | None:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            raw = bytes(payload)
            # Empty pickle frame cannot be decoded and must not crash reader callbacks.
            if self.codec.mode == "pickle" and not raw:
                return None
            try:
                return self.codec.decode(raw)
            except Exception:
                return None
        return payload


class _IpcReceiveBuffer:
    # Per-target receive buffer used by the in-memory adapter.
    # It preserves order and exposes basic queue metrics for observability.
    def __init__(self, policy: ExecutionIpcReceivePolicy) -> None:
        # deque holds ExecutionIpcMessage in FIFO order. Condition is used so
        # recv() can block while send() enqueues. This is the only buffering
        # mechanism in the in-memory adapter.
        self._queue: deque[ExecutionIpcMessage] = deque()
        self._condition = Condition()
        self._batch_max_items = max(1, int(policy.batch_max_items))
        self._flush_interval_ms = max(0, int(policy.flush_interval_ms))
        self._first_enqueued_at: float | None = None

    def enqueue(self, message: ExecutionIpcMessage) -> None:
        # Enqueue and wake any blocked receivers.
        with self._condition:
            if not self._queue:
                self._first_enqueued_at = time.monotonic()
            self._queue.append(message)
            self._condition.notify_all()

    def recv(self, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        # Blocking receive with timeout. This does not touch any pipe;
        # it's purely in-memory coordination between sender and receiver.
        deadline = _deadline(timeout)
        with self._condition:
            while not self._queue:
                remaining = _remaining(deadline)
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            message = self._queue.popleft()
            if not self._queue:
                self._first_enqueued_at = None
            return message

    def metrics(self) -> dict[str, object]:
        # Queue depth and age of the oldest message.
        with self._condition:
            queue_depth = len(self._queue)
            oldest_age_ms = 0
            if self._first_enqueued_at is not None:
                oldest_age_ms = max(0, int((time.monotonic() - self._first_enqueued_at) * 1000))
            return {
                "queue_depth": queue_depth,
                "oldest_age_ms": oldest_age_ms,
            }


class _PipeReceiveBuffer:
    # Buffer fed by a background pipe reader thread.
    # recv() waits on the Condition until data arrives or timeout elapses.
    def __init__(self) -> None:
        self._queue: deque[object] = deque()
        self._condition = Condition()
        self._first_enqueued_at: float | None = None

    def enqueue(self, payload: object) -> None:
        with self._condition:
            if not self._queue:
                self._first_enqueued_at = time.monotonic()
            self._queue.append(payload)
            self._condition.notify_all()

    def recv(self, *, timeout: float | None = None) -> object | None:
        deadline = _deadline(timeout)
        with self._condition:
            while not self._queue:
                remaining = _remaining(deadline)
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            payload = self._queue.popleft()
            if not self._queue:
                self._first_enqueued_at = None
            return payload

    def metrics(self) -> dict[str, object]:
        with self._condition:
            queue_depth = len(self._queue)
            oldest_age_ms = 0
            if self._first_enqueued_at is not None:
                oldest_age_ms = max(0, int((time.monotonic() - self._first_enqueued_at) * 1000))
            return {
                "queue_depth": queue_depth,
                "oldest_age_ms": oldest_age_ms,
            }


class _PipeSendBuffer:
    # Per-target outbound buffer consumed by a background sender thread.
    # send() enqueues and returns immediately; the sender performs blocking pipe writes.
    def __init__(self) -> None:
        self._queue: deque[object] = deque()
        self._condition = Condition()
        self._closed = False
        self._first_enqueued_at: float | None = None
        self._last_error: Exception | None = None

    def enqueue(self, payload: object) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("ipc outbound buffer is closed")
            if not self._queue:
                self._first_enqueued_at = time.monotonic()
            self._queue.append(payload)
            self._condition.notify_all()

    def requeue_left(self, payload: object) -> None:
        with self._condition:
            if self._closed:
                return
            if not self._queue:
                self._first_enqueued_at = time.monotonic()
            self._queue.appendleft(payload)
            self._condition.notify_all()

    def pop(self, *, timeout: float | None = None) -> object | None:
        deadline = _deadline(timeout)
        with self._condition:
            while not self._queue and not self._closed:
                remaining = _remaining(deadline)
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            if not self._queue:
                return None
            payload = self._queue.popleft()
            if not self._queue:
                self._first_enqueued_at = None
            return payload

    def set_error(self, exc: Exception) -> None:
        with self._condition:
            self._last_error = exc

    def clear_error(self) -> None:
        with self._condition:
            self._last_error = None

    def raise_if_error(self) -> None:
        with self._condition:
            exc = self._last_error
        if exc is not None:
            raise ConnectionError("ipc outbound sender is not healthy") from exc

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    def metrics(self) -> dict[str, object]:
        with self._condition:
            queue_depth = len(self._queue)
            oldest_age_ms = 0
            if self._first_enqueued_at is not None:
                oldest_age_ms = max(0, int((time.monotonic() - self._first_enqueued_at) * 1000))
            return {
                "outbound_queue_depth": queue_depth,
                "outbound_oldest_age_ms": oldest_age_ms,
            }


def _expand_batch_payload(payload: object) -> list[object]:
    # AdapterBatch allows callers to amortize transport overhead.
    if isinstance(payload, AdapterBatch):
        return list(payload.items)
    return [payload]


def _deadline(timeout: float | None) -> float | None:
    # Compute monotonic deadline for timeout handling.
    if timeout is None:
        return None
    return time.monotonic() + max(0.0, float(timeout))


def _remaining(deadline: float | None) -> float:
    # Convert deadline into remaining wait time.
    if deadline is None:
        return 3600.0
    return max(0.0, deadline - time.monotonic())


def _is_pipe_connection(candidate: object) -> bool:
    # Duck-typing: a pipe connection must support send() or send_bytes().
    if candidate is None:
        return False
    return callable(getattr(candidate, "send", None)) or callable(getattr(candidate, "send_bytes", None))


def _normalize_poll_mode(value: object) -> str:
    if not isinstance(value, str):
        return "reader"
    mode = value.strip().lower()
    if mode in {"reader", "timer", "auto"}:
        return mode
    return "reader"


def _flow_control_ack_target_id(target_id: str) -> str:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return target_id
    worker_id, lane = resolved
    if lane == "control":
        return target_id
    return compose_execution_ipc_worker_target_id(worker_id, lane="control")


def _target_uses_flow_control_ack(target_id: str) -> bool:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return True
    _worker_id, lane = resolved
    return lane in {EXECUTION_IPC_LANE_CONTROL, EXECUTION_IPC_LANE_DATA}


def _drain_pipe(
    endpoint: "_PipeEndpoint",
    buffer: _PipeReceiveBuffer,
    *,
    on_read: Callable[[], None] | None = None,
    on_ack: Callable[[ExecutionIpcControlSignal], None] | None = None,
    send_ack: Callable[[int], None] | None = None,
    ack_enabled: bool = False,
) -> int:
    # Drain all available payloads for this endpoint into the buffer.
    # Returns the number of user payloads enqueued.
    drained = 0
    while True:
        payload = endpoint.recv(timeout=0.0)
        if payload is None:
            break
        if isinstance(payload, ExecutionIpcControlSignal) and payload.kind == "ack":
            if on_ack is not None:
                on_ack(payload)
            if on_read is not None:
                on_read()
            continue
        buffer.enqueue(payload)
        drained += 1
        if on_read is not None:
            on_read()
    if drained > 0 and ack_enabled and callable(send_ack):
        send_ack(drained)
    return drained


def _send_ack(
    *,
    endpoint: "_PipeEndpoint",
    count: int,
    source_target_id: str,
) -> None:
    try:
        endpoint.send(
            ExecutionIpcControlSignal(
                kind="ack",
                count=int(count),
                target_id=source_target_id,
            )
        )
    except Exception:
        # ACKs are best-effort; never block the pipe reader on failures.
        return
