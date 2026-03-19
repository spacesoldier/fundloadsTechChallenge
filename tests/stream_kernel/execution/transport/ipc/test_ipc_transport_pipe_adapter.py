from __future__ import annotations

import time
from threading import Event

import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_codec import ExecutionIpcCodecError
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcControlSignal,
    ExecutionIpcEndpointRegistry,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.flow_control import (
    CreditWindowFlowControlPolicy,
    NoopFlowControlPolicy,
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


def test_pipe_ipc_service_roundtrip_payload() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:alpha")
    child.attach_endpoint(child_endpoint)

    ack = parent.send("group:alpha", {"id": 1, "value": "ok"})
    assert ack is not None
    assert ack.status == "accepted"
    assert ack.enqueued == 1

    message = child.recv("group:alpha", timeout=0.1)
    assert message is not None
    assert message.payload == {"id": 1, "value": "ok"}


def test_pipe_ipc_pickle_send_is_compatible_with_raw_connection_recv() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:raw-recv")

    ack = parent.send("group:raw-recv", {"kind": "hello"})
    assert ack is not None
    assert ack.status == "accepted"

    # Leaf control loop reads directly from raw pipe via connection.recv().
    payload = child_endpoint.connection.recv()
    assert payload == {"kind": "hello"}


def test_pipe_ipc_service_no_reply_returns_none() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:beta")
    child.attach_endpoint(child_endpoint)

    ack = parent.send("group:beta", "ping", no_reply=True)
    assert ack is None
    message = child.recv("group:beta", timeout=0.1)
    assert message is not None
    assert message.payload == "ping"


def test_pipe_ipc_service_buffered_recv_roundtrip_payload() -> None:
    registry = _EndpointRegistry()
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:buffered")
    child.attach_endpoint(child_endpoint)
    endpoint = child._resolve_endpoint("group:buffered")
    buffer = child._ensure_receive_buffer("group:buffered", endpoint)
    buffer.enqueue({"id": 7, "value": "buffered"})

    message = child.recv_buffered("group:buffered", timeout=0.1)
    assert message is not None
    assert message.payload == {"id": 7, "value": "buffered"}


def test_pipe_ipc_service_isolates_targets() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_a, child_a = parent.allocate_endpoints("group:a")
    _parent_b, child_b = parent.allocate_endpoints("group:b")
    child.attach_endpoint(child_a)
    child.attach_endpoint(child_b)

    parent.send("group:a", "msg-a")
    message_a = child.recv("group:a", timeout=0.1)
    message_b = child.recv("group:b", timeout=0.01)
    assert message_a is not None
    assert message_a.payload == "msg-a"
    assert message_b is None


def test_pipe_ipc_service_bytes_codec_rejects_unknown_payload() -> None:
    class _Payload:
        pass

    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="bytes", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="bytes", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:gamma")
    child.attach_endpoint(child_endpoint)

    with pytest.raises(ExecutionIpcCodecError):
        parent.send("group:gamma", _Payload())


def test_pipe_ipc_service_rejects_payload_larger_than_configured_limit() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(
        codec="bytes",
        endpoint_registry=registry,
        max_payload_bytes=8,
        respect_pipe_capacity=False,
    )
    child = PipeExecutionIpcTransportAdapter(codec="bytes", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:payload-limit")
    child.attach_endpoint(child_endpoint)

    with pytest.raises(ValueError, match="ipc payload exceeds configured/system pipe limit"):
        parent.send("group:payload-limit", b"0123456789")


def test_pipe_ipc_service_reports_payload_limit_metadata() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(
        codec="bytes",
        endpoint_registry=registry,
        max_payload_bytes=4096,
        respect_pipe_capacity=False,
    )
    _parent_endpoint, _child_endpoint = parent.allocate_endpoints("group:payload-limit-meta")

    limits = parent.describe_payload_limit("group:payload-limit-meta")

    assert limits["configured_max_payload_bytes"] == 4096
    assert limits["effective_max_payload_bytes"] == 4096
    assert limits["respect_pipe_capacity"] is False


def test_pipe_ipc_service_resolves_endpoint_from_registry() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    parent_endpoint, child_endpoint = parent.allocate_endpoints("group:delta")
    registry.set("group:delta", parent_endpoint.connection)
    child.attach_endpoint(child_endpoint)

    parent.send("group:delta", "hello")
    message = child.recv("group:delta", timeout=0.1)
    assert message is not None
    assert message.payload == "hello"


def test_pipe_ipc_service_tolerates_empty_pickle_frame() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:empty-frame")

    # Simulate malformed/empty frame from the other end.
    child_endpoint.connection.send_bytes(b"")

    # Adapter must not raise from recv() or crash the reader callback.
    assert parent.recv("group:empty-frame", timeout=0.01) is None

    # Transport should continue working after the malformed frame.
    child_endpoint.send("ok-after-empty")
    message = parent.recv("group:empty-frame", timeout=0.1)
    assert message is not None
    assert message.payload == "ok-after-empty"


def test_pipe_ipc_service_lazy_attach_from_registry() -> None:
    registry = _EndpointRegistry()
    adapter = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    parent_endpoint, _child_endpoint = adapter.allocate_endpoints("group:epsilon")
    registry.set("group:epsilon", parent_endpoint.connection)

    adapter.send("group:epsilon", "ping")


def test_pipe_ipc_send_path_does_not_start_reader_loop() -> None:
    registry = _EndpointRegistry()
    adapter = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, _child_endpoint = adapter.allocate_endpoints("group:send-only")

    ack = adapter.send("group:send-only", "ping")
    assert ack is not None
    assert ack.status == "accepted"
    # Send-only path must not register a receive reader. Otherwise it can race
    # with leaf control ingress, which reads the same control pipe directly.
    assert "group:send-only" not in adapter._reader_targets
    assert adapter._loop is None
    assert adapter._loop_thread is None


def test_pipe_ipc_adapter_poll_mode_default_reader() -> None:
    adapter = PipeExecutionIpcTransportAdapter()
    assert adapter._poll_mode == "reader"


def test_pipe_ipc_adapter_ack_signal_is_consumed() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    _parent_endpoint, child_endpoint = parent.allocate_endpoints("group:ack")
    child.attach_endpoint(child_endpoint)

    acked: list[int] = []
    ready = Event()

    def _handler(count: int) -> None:
        acked.append(count)
        ready.set()

    parent.register_ack_handler("group:ack", _handler)
    parent.enable_ack(True)
    child.enable_ack(True)

    # Ensure reader is registered.
    parent.recv("group:ack", timeout=0.01)
    # Send a control signal from child -> parent.
    child_endpoint.send(ExecutionIpcControlSignal(kind="ack", count=3))
    # Force a drain pass to process the ack promptly.
    parent.recv("group:ack", timeout=0.01)

    assert ready.wait(timeout=0.2)
    assert acked == [3]
    # Ack should not surface as a user payload.
    assert parent.recv("group:ack", timeout=0.01) is None


def test_pipe_ipc_adapter_send_ack_for_target_uses_async_sender_buffer() -> None:
    registry = _EndpointRegistry()
    adapter = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    data_target = compose_execution_ipc_worker_target_id("execution.alpha#1", lane=EXECUTION_IPC_LANE_DATA)
    control_target = compose_execution_ipc_worker_target_id("execution.alpha#1", lane="control")
    _parent_data, _child_data = adapter.allocate_endpoints(data_target)
    _parent_control, _child_control = adapter.allocate_endpoints(control_target)

    enqueued: list[object] = []

    class _DummySendBuffer:
        def enqueue(self, payload: object) -> None:
            enqueued.append(payload)

    def _resolve_endpoint(_target_id: str):  # noqa: ANN001
        # Endpoint availability is checked, but send() must not be called here.
        return object()

    def _ensure_send_buffer(_target_id: str):  # noqa: ANN001
        return _DummySendBuffer()

    adapter._resolve_endpoint = _resolve_endpoint  # type: ignore[method-assign]
    adapter._ensure_send_buffer = _ensure_send_buffer  # type: ignore[method-assign]

    adapter._send_ack_for_target(
        target_id=data_target,
        endpoint=object(),  # not used by async ACK path
        count=5,
    )

    assert len(enqueued) == 1
    ack = enqueued[0]
    assert isinstance(ack, ExecutionIpcControlSignal)
    assert ack.kind == "ack"
    assert ack.count == 5
    assert ack.target_id == data_target


def test_ipc_transport_coordinator_buffers_until_endpoint_is_registered() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    service = ExecutionIpcTransportCoordinatorService(
        adapter=parent,
        endpoint_registry=registry,
        flow_control=NoopFlowControlPolicy(),
    )

    first_ack = service.send("group:late", "early-1")
    second_ack = service.send("group:late", "early-2")
    assert first_ack is not None
    assert second_ack is not None
    assert first_ack.status == "buffered"
    assert second_ack.status == "buffered"
    assert first_ack.enqueued == 1
    assert second_ack.enqueued == 1

    parent_endpoint, child_endpoint = parent.allocate_endpoints("group:late")
    registry.set("group:late", parent_endpoint.connection)
    child.attach_endpoint(child_endpoint)

    final_ack = service.send("group:late", "late-3")
    assert final_ack is not None
    assert final_ack.status == "accepted"
    assert final_ack.enqueued == 1

    first = child.recv("group:late", timeout=0.1)
    second = child.recv("group:late", timeout=0.1)
    third = child.recv("group:late", timeout=0.1)
    assert first is not None and first.payload == "early-1"
    assert second is not None and second.payload == "early-2"
    assert third is not None and third.payload == "late-3"


def test_ipc_transport_coordinator_flush_pending_is_idempotent() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    service = ExecutionIpcTransportCoordinatorService(
        adapter=parent,
        endpoint_registry=registry,
        flow_control=NoopFlowControlPolicy(),
    )

    service.send("group:late-flush", "early", no_reply=True)
    # No endpoint yet: flush should be a no-op and keep pending intact.
    service.flush_pending("group:late-flush")

    parent_endpoint, child_endpoint = parent.allocate_endpoints("group:late-flush")
    registry.set("group:late-flush", parent_endpoint.connection)
    child.attach_endpoint(child_endpoint)

    flushed = service.flush_pending("group:late-flush")
    assert flushed == 1
    assert child.recv("group:late-flush", timeout=0.1).payload == "early"
    # Second flush should be safe and do nothing.
    assert service.flush_pending("group:late-flush") == 0


def test_ipc_transport_coordinator_does_not_collapse_non_control_lane_to_worker_target() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    service = ExecutionIpcTransportCoordinatorService(
        adapter=parent,
        endpoint_registry=registry,
        flow_control=NoopFlowControlPolicy(),
    )

    # Register only a plain worker target endpoint (control lane shape).
    worker_id = "execution.ingress#1"
    parent_endpoint, child_endpoint = parent.allocate_endpoints(worker_id)
    registry.set(worker_id, parent_endpoint.connection)
    child.attach_endpoint(child_endpoint)

    data_lane_target = compose_execution_ipc_worker_target_id(
        worker_id,
        lane=EXECUTION_IPC_LANE_DATA,
    )
    ack = service.send(data_lane_target, "lane-payload")
    assert ack is not None
    # Non-control lanes must stay isolated and buffer until their own endpoint exists.
    assert ack.status == "buffered"

    # Ensure payload was not misrouted into the worker/control target.
    assert child.recv(worker_id, timeout=0.01) is None


def test_ipc_transport_coordinator_send_is_non_blocking_when_credit_window_is_exhausted() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    service = ExecutionIpcTransportCoordinatorService(
        adapter=parent,
        endpoint_registry=registry,
        flow_control=CreditWindowFlowControlPolicy(window_size=1),
    )

    worker_id = "execution.ingress#1"
    data_target = compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_DATA)
    control_target = worker_id
    _parent_data, child_data = parent.allocate_endpoints(data_target)
    _parent_control, child_control = parent.allocate_endpoints(control_target)
    child.attach_endpoint(child_data)
    child.attach_endpoint(child_control)
    child.enable_ack(True)

    first = service.send(data_target, {"id": 1})
    assert first is not None
    assert first.status == "accepted"

    start = time.monotonic()
    second = service.send(data_target, {"id": 2})
    elapsed = time.monotonic() - start
    assert second is not None
    assert second.status == "buffered"
    # Non-blocking contract: send path must not wait for downstream ack.
    assert elapsed < 0.05

    # Consume first data payload from child.
    first_payload = child.recv(data_target, timeout=0.1)
    assert first_payload is not None
    assert first_payload.payload == {"id": 1}
    # Pump control lane until ack is observed and pending payload is flushed.
    second_payload = None
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and second_payload is None:
        _ = parent.recv(control_target, timeout=0.01)
        _ = service.flush_pending(data_target)
        second_payload = child.recv(data_target, timeout=0.01)
    assert second_payload is not None
    assert second_payload.payload == {"id": 2}


def test_pipe_ipc_adapter_does_not_emit_flow_control_ack_for_trace_lane() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)

    worker_id = "execution.ingress#1"
    trace_target = compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_TRACE)
    control_target = worker_id

    _parent_trace, child_trace = parent.allocate_endpoints(trace_target)
    _parent_control, child_control = parent.allocate_endpoints(control_target)
    child.attach_endpoint(child_trace)
    child.attach_endpoint(child_control)

    parent.enable_ack(True)
    child.enable_ack(True)

    acked: list[int] = []

    def _on_ack(payload: object) -> None:
        if isinstance(payload, ExecutionIpcControlSignal):
            acked.append(int(payload.count))
            return
        acked.append(int(payload))

    child.register_ack_handler(control_target, _on_ack)

    child.send(trace_target, {"id": 1})
    received = parent.recv(trace_target, timeout=0.1)
    assert received is not None
    assert received.payload == {"id": 1}

    # Drain control lane if any signal was generated.
    _ = child.recv(control_target, timeout=0.05)
    assert acked == []


def test_ipc_transport_coordinator_trace_lane_bypasses_credit_window_and_stays_non_blocking() -> None:
    registry = _EndpointRegistry()
    parent = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    child = PipeExecutionIpcTransportAdapter(codec="pickle", endpoint_registry=registry)
    service = ExecutionIpcTransportCoordinatorService(
        adapter=parent,
        endpoint_registry=registry,
        flow_control=CreditWindowFlowControlPolicy(window_size=1),
    )

    worker_id = "system.observability#1"
    trace_target = compose_execution_ipc_worker_target_id(worker_id, lane=EXECUTION_IPC_LANE_TRACE)
    _parent_trace, child_trace = parent.allocate_endpoints(trace_target)
    child.attach_endpoint(child_trace)

    first = service.send(trace_target, {"id": 1})
    assert first is not None
    assert first.status == "accepted"

    start = time.monotonic()
    second = service.send(trace_target, {"id": 2})
    elapsed = time.monotonic() - start
    assert second is not None
    assert second.status == "accepted"
    assert elapsed < 0.05

    first_payload = child.recv(trace_target, timeout=0.2)
    second_payload = child.recv(trace_target, timeout=0.2)
    assert first_payload is not None and first_payload.payload == {"id": 1}
    assert second_payload is not None and second_payload.payload == {"id": 2}


def test_ipc_transport_coordinator_recv_buffered_prefers_adapter_path() -> None:
    class _Adapter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def send(self, target_id: str, payload: object, *, no_reply: bool = False):
            _ = (target_id, payload, no_reply)
            return None

        def recv(self, target_id: str, *, timeout: float | None = None):
            _ = (target_id, timeout)
            self.calls.append("recv")
            return ExecutionIpcControlSignal(kind="unexpected")

        def recv_buffered(self, target_id: str, *, timeout: float | None = None):
            _ = (target_id, timeout)
            self.calls.append("recv_buffered")
            return "buffered"

        def metrics(self, target_id: str) -> dict[str, object]:
            _ = target_id
            return {}

        def build_port(self, *, target_id: str | None = None, receive_policy: object | None = None):
            _ = (target_id, receive_policy)
            return None

    service = ExecutionIpcTransportCoordinatorService(
        adapter=_Adapter(),  # type: ignore[arg-type]
        endpoint_registry=_EndpointRegistry(),
        flow_control=NoopFlowControlPolicy(),
    )

    payload = service.recv_buffered("group:buffered-path", timeout=0.01)

    assert payload == "buffered"
    assert service.adapter.calls == ["recv_buffered"]  # type: ignore[attr-defined]
