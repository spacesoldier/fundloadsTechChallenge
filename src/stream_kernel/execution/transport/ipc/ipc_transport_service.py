from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from stream_kernel.adapters.contracts import AdapterBatch
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    InMemoryExecutionIpcTransportAdapter,
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.flow_control import (
    ExecutionIpcFlowControlPolicy,
    NoopFlowControlPolicy,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcAck,
    ExecutionIpcControlSignal,
    ExecutionIpcEndpointRegistry,
    ExecutionIpcKvStreamPort,
    ExecutionIpcPort,
    ExecutionIpcReceivePolicy,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
    decompose_execution_ipc_worker_target_id,
)
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.debug_buffer import (
    RuntimeDebugBufferService,
    debug_instrument_service_methods,
    publish_runtime_debug,
)


@service(name="execution_ipc_transport_service")
@debug_instrument_service_methods
@dataclass(slots=True)
class ExecutionIpcTransportCoordinatorService(ExecutionIpcTransportService):
    adapter: ExecutionIpcKvStreamPort = inject.kv_stream(ExecutionIpcKvStreamPort)
    endpoint_registry: object = inject.kv(ExecutionIpcEndpointRegistry)
    runtime_debug_buffer: object | None = inject.service(RuntimeDebugBufferService)
    flow_control: ExecutionIpcFlowControlPolicy = field(
        default_factory=NoopFlowControlPolicy
    )
    _pending_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _flow_control_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _flow_control_registered_targets: set[str] = field(default_factory=set, init=False, repr=False)
    _payload_limit_logged_targets: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.flow_control.requires_ack:
            return
        enable_ack = getattr(self.adapter, "enable_ack", None)
        if callable(enable_ack):
            enable_ack(True)

    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ):
        resolved_target_id = self._resolve_transport_target_id(target_id)
        try:
            self._ensure_endpoint(resolved_target_id)
            self._flush_pending(resolved_target_id)
            sent, ack = self._try_send_now(resolved_target_id, payload, no_reply=no_reply)
            if sent:
                self._emit_ipc_debug(
                    event="ipc.send",
                    target_id=resolved_target_id,
                    payload=payload,
                    status="sent",
                )
                return ack
            self._enqueue_pending(
                target_id=resolved_target_id,
                payload=payload,
                no_reply=no_reply,
            )
            self._emit_ipc_debug(
                event="ipc.send",
                target_id=resolved_target_id,
                payload=payload,
                status="buffered",
            )
            if no_reply:
                return None
            return self._buffered_ack(payload)
        except Exception as exc:
            if self._should_buffer_until_endpoint_ready(target_id=resolved_target_id, error=exc):
                self._enqueue_pending(target_id=resolved_target_id, payload=payload, no_reply=no_reply)
                if no_reply:
                    return None
                return self._buffered_ack(payload)
            raise

    def recv(self, target_id: str, *, timeout: float | None = None):
        resolved_target_id = self._resolve_transport_target_id(target_id)
        self._ensure_endpoint(resolved_target_id)
        self._flush_pending(resolved_target_id)
        message = self.adapter.recv(resolved_target_id, timeout=timeout)
        if message is not None:
            payload = getattr(message, "payload", None)
            self._emit_ipc_debug(
                event="ipc.recv",
                target_id=resolved_target_id,
                payload=payload,
                status="received",
            )
        return message

    def recv_buffered(self, target_id: str, *, timeout: float | None = None):
        resolved_target_id = self._resolve_transport_target_id(target_id)
        self._ensure_endpoint(resolved_target_id)
        self._flush_pending(resolved_target_id)
        recv_buffered = getattr(self.adapter, "recv_buffered", None)
        if callable(recv_buffered):
            message = recv_buffered(resolved_target_id, timeout=timeout)
        else:
            message = self.adapter.recv(resolved_target_id, timeout=timeout)
        if message is not None:
            payload = getattr(message, "payload", None)
            self._emit_ipc_debug(
                event="ipc.recv_buffered",
                target_id=resolved_target_id,
                payload=payload,
                status="received",
            )
        return message

    def metrics(self, target_id: str) -> dict[str, object]:
        resolved_target_id = self._resolve_transport_target_id(target_id)
        data = dict(self.adapter.metrics(resolved_target_id))
        data["pending_outbound"] = self._pending_count(resolved_target_id)
        return data

    def flush_pending(self, target_id: str) -> int:
        resolved_target_id = self._resolve_transport_target_id(target_id)
        try:
            self._ensure_endpoint(resolved_target_id)
            return self._flush_pending(resolved_target_id)
        except Exception as exc:
            if self._should_buffer_until_endpoint_ready(target_id=resolved_target_id, error=exc):
                return 0
            raise

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort:
        # DI-only hook: registers receive-side buffering and returns a bound port.
        if receive_policy is not None:
            _ = self.adapter.build_port(target_id=target_id, receive_policy=receive_policy)
        return ExecutionIpcPort(service=self, target_id=target_id)

    def allocate_local_endpoints(
        self,
        target_id: str,
        *,
        register_parent_endpoint: bool = True,
    ) -> tuple[object, object]:
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionIpcTransportService.allocate_local_endpoints requires non-empty target_id")
        allocate = getattr(self.adapter, "allocate_endpoints", None)
        if not callable(allocate):
            raise ValueError("ExecutionIpcTransportService adapter does not support endpoint allocation")
        parent_endpoint, child_endpoint = allocate(target_id)
        parent = getattr(parent_endpoint, "connection", parent_endpoint)
        child = getattr(child_endpoint, "connection", child_endpoint)
        registry = self._endpoint_store()
        if register_parent_endpoint and registry is not None:
            registry.set(target_id, parent)
        return parent, child

    def bind_local_endpoint(self, target_id: str, endpoint: object) -> None:
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionIpcTransportService.bind_local_endpoint requires non-empty target_id")
        attach = getattr(self.adapter, "attach_endpoint", None)
        if not callable(attach):
            raise ValueError("ExecutionIpcTransportService adapter does not support local endpoint binding")
        try:
            attach(endpoint, target_id=target_id)
        except TypeError:
            attach(endpoint)
        registry = self._endpoint_store()
        if registry is not None:
            registry.set(target_id, endpoint)

    def register_data_available_callback(
        self,
        target_id: str,
        callback: object,
        *,
        loop: object | None = None,
    ) -> bool:
        if not isinstance(target_id, str) or not target_id:
            return False
        if not callable(callback):
            return False
        resolved_target_id = self._resolve_transport_target_id(target_id)
        try:
            self._ensure_endpoint(resolved_target_id)
        except Exception:
            return False
        register = getattr(self.adapter, "register_data_available_callback", None)
        if not callable(register):
            return False
        try:
            result = register(resolved_target_id, callback, loop=loop)
            if isinstance(result, bool):
                return result
            return True
        except Exception:
            return False

    def _ensure_flow_control(self, target_id: str) -> None:
        if not self.flow_control.requires_ack or not _uses_credit_window(target_id):
            return
        ack_target_id = _flow_control_ack_target_id(target_id)
        with self._flow_control_lock:
            if ack_target_id in self._flow_control_registered_targets:
                return
            register = getattr(self.adapter, "register_ack_handler", None)
            enable_ack = getattr(self.adapter, "enable_ack", None)
            if callable(enable_ack):
                enable_ack(True)
            if callable(register):
                register(
                    ack_target_id,
                    lambda payload: self._on_flow_control_ack(ack_target_id, payload),
                )
            self._flow_control_registered_targets.add(ack_target_id)

    def _resolve_transport_target_id(self, target_id: str) -> str:
        if not isinstance(target_id, str) or not target_id:
            return target_id
        endpoint_store = self._endpoint_store()
        if endpoint_store is None:
            return target_id
        if endpoint_store.get(target_id) is not None:
            return target_id
        resolved = decompose_execution_ipc_worker_target_id(target_id)
        if resolved is None:
            return target_id
        _worker_id, lane = resolved
        if lane == "control":
            return target_id
        # Keep non-control lanes isolated. Falling back to worker_id collapses
        # lane traffic into the control pipe and can reintroduce head-of-line
        # blocking under load.
        return target_id

    def _ensure_endpoint(self, target_id: str) -> None:
        registry = self._endpoint_store()
        if registry is None:
            return
        endpoint = registry.get(target_id)
        if endpoint is None:
            return
        attach = getattr(self.adapter, "attach_endpoint", None)
        if callable(attach):
            try:
                attach(endpoint, target_id=target_id)
            except TypeError:
                attach(endpoint)
        self._emit_payload_limit_debug_once(target_id=target_id)

    def _emit_payload_limit_debug_once(self, *, target_id: str) -> None:
        with self._pending_lock:
            if target_id in self._payload_limit_logged_targets:
                return
        describe_limits = getattr(self.adapter, "describe_payload_limit", None)
        if not callable(describe_limits):
            return
        try:
            payload = describe_limits(target_id)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        fields = {
            "target_id": target_id,
            "lane": _lane_from_target_id(target_id),
            "configured_max_payload_bytes": payload.get("configured_max_payload_bytes"),
            "pipe_capacity_bytes": payload.get("pipe_capacity_bytes"),
            "effective_max_payload_bytes": payload.get("effective_max_payload_bytes"),
            "respect_pipe_capacity": payload.get("respect_pipe_capacity"),
        }
        publish_runtime_debug(
            buffer=self.runtime_debug_buffer,
            event="ipc.payload_limit.detected",
            source="stream_kernel.execution.transport.ipc",
            fields=fields,
            trace_id=None,
        )
        with self._pending_lock:
            self._payload_limit_logged_targets.add(target_id)

    def _endpoint_store(self) -> KVStore | None:
        if isinstance(self.endpoint_registry, KVStore):
            return self.endpoint_registry
        return None

    def _pending_kv(self) -> KVStore | None:
        # Reuse injected endpoint registry store with namespaced keys for pending outbound backlog.
        # This keeps transport state on platform rails without requiring an extra KV binding everywhere.
        return self._endpoint_store()

    def _try_send_now(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ) -> tuple[bool, ExecutionIpcAck | None]:
        if not _uses_credit_window(target_id):
            return (True, self.adapter.send(target_id, payload, no_reply=no_reply))
        self._ensure_flow_control(target_id)
        count = _payload_count(payload)
        if not self.flow_control.try_acquire(target_id, count):
            return (False, None)
        try:
            return (True, self.adapter.send(target_id, payload, no_reply=no_reply))
        except Exception:
            self.flow_control.release(target_id, count)
            raise

    def _flush_pending(self, target_id: str) -> int:
        with self._pending_lock:
            entries = self._pending_entries_unlocked(target_id)
            if not entries:
                return 0
            flushed = 0
            remaining: list[dict[str, object]] = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                payload = entry.get("payload")
                no_reply = bool(entry.get("no_reply", False))
                sent, _ack = self._try_send_now(target_id, payload, no_reply=no_reply)
                if sent:
                    flushed += _payload_count(payload)
                    continue
                remaining.append({"payload": payload, "no_reply": no_reply})
                for tail in entries[index + 1 :]:
                    if isinstance(tail, dict):
                        remaining.append(dict(tail))
                break
            if remaining:
                self._set_pending_entries_unlocked(target_id, remaining)
            else:
                self._clear_pending_unlocked(target_id)
            return flushed

    def _enqueue_pending(self, *, target_id: str, payload: object, no_reply: bool) -> None:
        store = self._pending_kv()
        if store is None:
            raise ConnectionError(f"ipc transport endpoint not registered for target '{target_id}'")
        with self._pending_lock:
            entries = self._pending_entries_unlocked(target_id)
            entries.append({"payload": payload, "no_reply": bool(no_reply)})
            self._set_pending_entries_unlocked(target_id, entries)

    def _pending_entries(self, target_id: str) -> list[dict[str, object]]:
        with self._pending_lock:
            return self._pending_entries_unlocked(target_id)

    def _pending_entries_unlocked(self, target_id: str) -> list[dict[str, object]]:
        store = self._pending_kv()
        if store is None:
            return []
        raw = store.get(_pending_key(target_id))
        if not isinstance(raw, list):
            return []
        entries: list[dict[str, object]] = []
        for item in raw:
            if isinstance(item, dict):
                entries.append(dict(item))
        return entries

    def _set_pending_entries_unlocked(self, target_id: str, entries: list[dict[str, object]]) -> None:
        store = self._pending_kv()
        if store is None:
            return
        store.set(_pending_key(target_id), list(entries))

    def _clear_pending(self, target_id: str) -> None:
        with self._pending_lock:
            self._clear_pending_unlocked(target_id)

    def _clear_pending_unlocked(self, target_id: str) -> None:
        store = self._pending_kv()
        if store is None:
            return
        store.delete(_pending_key(target_id))

    def _pending_count(self, target_id: str) -> int:
        with self._pending_lock:
            return len(self._pending_entries_unlocked(target_id))

    def _on_flow_control_ack(self, ack_target_id: str, payload: object) -> None:
        count, source_target_id = _decode_ack_payload(payload=payload, ack_target_id=ack_target_id)
        if count <= 0:
            return
        self.flow_control.release(source_target_id, count)
        self._emit_ipc_debug(
            event="ipc.flow_control.ack",
            target_id=source_target_id,
            payload=payload,
            status="released",
            count=count,
        )
        try:
            self._flush_pending(source_target_id)
        except Exception:
            # Ack callback must stay failure-isolated.
            return

    def _should_buffer_until_endpoint_ready(self, *, target_id: str, error: Exception) -> bool:
        if self._pending_kv() is None:
            return False
        if not isinstance(error, ConnectionError):
            return False
        endpoint_store = self._endpoint_store()
        if endpoint_store is None:
            return False
        # Buffer only the "endpoint not registered yet" case, not generic connection failures.
        if endpoint_store.get(target_id) is not None:
            return False
        return "endpoint not registered" in str(error)

    @staticmethod
    def _buffered_ack(payload: object) -> ExecutionIpcAck:
        count = _payload_count(payload)
        return ExecutionIpcAck(status="buffered", enqueued=max(0, int(count)))

    def _emit_ipc_debug(
        self,
        *,
        event: str,
        target_id: str,
        payload: object,
        status: str,
        count: int | None = None,
    ) -> None:
        if _is_runtime_debug_payload(payload):
            return
        fields: dict[str, object] = {
            "target_id": target_id,
            "lane": _lane_from_target_id(target_id),
            "status": status,
            "payload_type": _payload_type_name(payload),
            "payload_count": _payload_count(payload),
        }
        if isinstance(count, int) and count > 0:
            fields["count"] = count
        publish_runtime_debug(
            buffer=self.runtime_debug_buffer,
            event=event,
            source="stream_kernel.execution.transport.ipc",
            fields=fields,
            trace_id=None,
        )


def _payload_count(payload: object) -> int:
    if isinstance(payload, AdapterBatch):
        return max(0, len(payload.items))
    return 1


def _payload_type_name(payload: object) -> str:
    token = payload if isinstance(payload, type) else payload.__class__
    module = getattr(token, "__module__", None)
    qualname = getattr(token, "__qualname__", None)
    if isinstance(module, str) and module and isinstance(qualname, str) and qualname:
        return f"{module}.{qualname}"
    name = getattr(token, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return type(payload).__name__


def _is_runtime_debug_payload(payload: object) -> bool:
    name = _payload_type_name(payload)
    return name.endswith("DebugMessage") or name.endswith("DebugDispatchEvent")


def _lane_from_target_id(target_id: str) -> str:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return "unknown"
    _worker_id, lane = resolved
    return lane


def _pending_key(target_id: str) -> str:
    return f"execution_ipc.pending_outbound:{target_id}"


def _uses_credit_window(target_id: str) -> bool:
    lane = _lane_from_target_id(target_id)
    if lane == "unknown":
        return True
    return lane in {EXECUTION_IPC_LANE_CONTROL, EXECUTION_IPC_LANE_DATA}


def _flow_control_ack_target_id(target_id: str) -> str:
    resolved = decompose_execution_ipc_worker_target_id(target_id)
    if resolved is None:
        return target_id
    worker_id, lane = resolved
    if lane == "control":
        return target_id
    return compose_execution_ipc_worker_target_id(worker_id, lane="control")


def _decode_ack_payload(*, payload: object, ack_target_id: str) -> tuple[int, str]:
    if isinstance(payload, ExecutionIpcControlSignal):
        source_target_id = (
            payload.target_id
            if isinstance(payload.target_id, str) and payload.target_id
            else ack_target_id
        )
        return (max(0, int(payload.count)), source_target_id)
    if isinstance(payload, int):
        return (max(0, int(payload)), ack_target_id)
    count = getattr(payload, "count", None)
    source_target_id = getattr(payload, "target_id", None)
    if isinstance(count, int):
        return (
            max(0, int(count)),
            source_target_id if isinstance(source_target_id, str) and source_target_id else ack_target_id,
        )
    return (0, ack_target_id)


__all__ = [
    "ExecutionIpcTransportCoordinatorService",
    "InMemoryExecutionIpcTransportAdapter",
    "PipeExecutionIpcTransportAdapter",
]
