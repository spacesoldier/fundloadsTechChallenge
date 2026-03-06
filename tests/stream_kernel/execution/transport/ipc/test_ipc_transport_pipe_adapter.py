from __future__ import annotations

from threading import Event

import pytest

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_codec import ExecutionIpcCodecError
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcControlSignal,
    ExecutionIpcEndpointRegistry,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.flow_control import NoopFlowControlPolicy


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
