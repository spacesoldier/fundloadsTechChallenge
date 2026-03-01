from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (
    InMemoryExecutionIpcTransportAdapter,
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcAck,
    ExecutionIpcEndpointRegistry,
    ExecutionIpcKvStreamPort,
    ExecutionIpcPort,
    ExecutionIpcReceivePolicy,
    ExecutionIpcTransportService,
)
from stream_kernel.execution.transport.ipc.flow_control import (
    ExecutionIpcFlowControlPolicy,
    CreditWindowFlowControlPolicy,
    NoopFlowControlPolicy,
)
from stream_kernel.adapters.contracts import AdapterBatch


@service(name="execution_ipc_transport_service")
@dataclass(slots=True)
class ExecutionIpcTransportCoordinatorService(ExecutionIpcTransportService):
    adapter: ExecutionIpcKvStreamPort = inject.kv_stream(ExecutionIpcKvStreamPort)
    endpoint_registry: object = inject.kv(ExecutionIpcEndpointRegistry)
    flow_control: ExecutionIpcFlowControlPolicy = field(
        default_factory=lambda: CreditWindowFlowControlPolicy(window_size=16)
    )

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
        try:
            self._ensure_endpoint(target_id)
            self._flush_pending(target_id)
            return self._send_now(target_id, payload, no_reply=no_reply)
        except Exception as exc:
            if self._should_buffer_until_endpoint_ready(target_id=target_id, error=exc):
                self._enqueue_pending(target_id=target_id, payload=payload, no_reply=no_reply)
                if no_reply:
                    return None
                return self._buffered_ack(payload)
            raise

    def recv(self, target_id: str, *, timeout: float | None = None):
        self._ensure_endpoint(target_id)
        self._flush_pending(target_id)
        return self.adapter.recv(target_id, timeout=timeout)

    def metrics(self, target_id: str) -> dict[str, object]:
        data = dict(self.adapter.metrics(target_id))
        data["pending_outbound"] = self._pending_count(target_id)
        return data

    def flush_pending(self, target_id: str) -> int:
        try:
            self._ensure_endpoint(target_id)
            return self._flush_pending(target_id)
        except Exception as exc:
            if self._should_buffer_until_endpoint_ready(target_id=target_id, error=exc):
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

    def allocate_local_endpoints(self, target_id: str) -> tuple[object, object]:
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionIpcTransportService.allocate_local_endpoints requires non-empty target_id")
        allocate = getattr(self.adapter, "allocate_endpoints", None)
        if not callable(allocate):
            raise ValueError("ExecutionIpcTransportService adapter does not support endpoint allocation")
        parent_endpoint, child_endpoint = allocate(target_id)
        parent = getattr(parent_endpoint, "connection", parent_endpoint)
        child = getattr(child_endpoint, "connection", child_endpoint)
        registry = self._endpoint_store()
        if registry is not None:
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

    def _ensure_flow_control(self, target_id: str) -> None:
        if not self.flow_control.requires_ack:
            return
        register = getattr(self.adapter, "register_ack_handler", None)
        enable_ack = getattr(self.adapter, "enable_ack", None)
        if callable(enable_ack):
            enable_ack(True)
        if callable(register):
            register(
                target_id,
                lambda count, _target=target_id: self.flow_control.release(_target, count),
            )

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

    def _endpoint_store(self) -> KVStore | None:
        if isinstance(self.endpoint_registry, KVStore):
            return self.endpoint_registry
        return None

    def _pending_kv(self) -> KVStore | None:
        # Reuse injected endpoint registry store with namespaced keys for pending outbound backlog.
        # This keeps transport state on platform rails without requiring an extra KV binding everywhere.
        return self._endpoint_store()

    def _send_now(self, target_id: str, payload: object, *, no_reply: bool = False):
        self._ensure_flow_control(target_id)
        count = _payload_count(payload)
        self.flow_control.acquire(target_id, count)
        try:
            return self.adapter.send(target_id, payload, no_reply=no_reply)
        except Exception:
            self.flow_control.release(target_id, count)
            raise

    def _flush_pending(self, target_id: str) -> int:
        entries = self._pending_entries(target_id)
        if not entries:
            return 0
        # Keep insertion order and only clear storage after successful flush.
        flushed = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            no_reply = bool(entry.get("no_reply", False))
            self._send_now(target_id, payload, no_reply=no_reply)
            flushed += _payload_count(payload)
        self._clear_pending(target_id)
        return flushed

    def _enqueue_pending(self, *, target_id: str, payload: object, no_reply: bool) -> None:
        store = self._pending_kv()
        if store is None:
            raise ConnectionError(f"ipc transport endpoint not registered for target '{target_id}'")
        entries = self._pending_entries(target_id)
        entries.append({"payload": payload, "no_reply": bool(no_reply)})
        store.set(_pending_key(target_id), entries)

    def _pending_entries(self, target_id: str) -> list[dict[str, object]]:
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

    def _clear_pending(self, target_id: str) -> None:
        store = self._pending_kv()
        if store is None:
            return
        store.delete(_pending_key(target_id))

    def _pending_count(self, target_id: str) -> int:
        return len(self._pending_entries(target_id))

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


def _payload_count(payload: object) -> int:
    if isinstance(payload, AdapterBatch):
        return max(0, len(payload.items))
    return 1


def _pending_key(target_id: str) -> str:
    return f"execution_ipc.pending_outbound:{target_id}"


__all__ = [
    "ExecutionIpcTransportCoordinatorService",
    "InMemoryExecutionIpcTransportAdapter",
    "PipeExecutionIpcTransportAdapter",
]
