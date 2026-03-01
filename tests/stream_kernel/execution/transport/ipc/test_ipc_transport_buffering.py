from __future__ import annotations

import time

from stream_kernel.adapters.contracts import AdapterBatch
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcReceivePolicy
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    InMemoryExecutionIpcTransportAdapter,
)


def test_ipc_transport_registers_buffered_receiver_in_kv_store() -> None:
    store = InMemoryKvStore()
    service = InMemoryExecutionIpcTransportAdapter(kv_store=store)
    port = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(buffer_enabled=True, batch_max_items=4, flush_interval_ms=5),
    )
    _ = port
    registry = store.get("ipc.endpoint_registry")
    assert isinstance(registry, dict)
    assert "worker:alpha#1" in registry
    assert registry["worker:alpha#1"]["buffer_enabled"] is True


def test_ipc_transport_ack_and_ordered_receive() -> None:
    service = InMemoryExecutionIpcTransportAdapter()
    receiver = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(batch_max_items=8, flush_interval_ms=1000),
    )
    sender = service.build_port()
    ack = sender.send("worker:alpha#1", {"value": 1})
    assert ack is not None
    assert ack.status == "accepted"
    ack = sender.send("worker:alpha#1", {"value": 2})
    assert ack is not None

    first = receiver.recv(timeout=0.1)
    second = receiver.recv(timeout=0.1)
    assert first is not None
    assert second is not None
    assert first.payload == {"value": 1}
    assert second.payload == {"value": 2}


def test_ipc_transport_expands_adapter_batch_into_items() -> None:
    service = InMemoryExecutionIpcTransportAdapter()
    receiver = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(batch_max_items=8, flush_interval_ms=1000),
    )
    sender = service.build_port()
    batch = AdapterBatch(items=[1, 2, 3], item_type=int)
    sender.send("worker:alpha#1", batch)

    assert receiver.recv(timeout=0.1).payload == 1
    assert receiver.recv(timeout=0.1).payload == 2
    assert receiver.recv(timeout=0.1).payload == 3


def test_ipc_transport_batch_flushes_on_max_items() -> None:
    service = InMemoryExecutionIpcTransportAdapter()
    receiver = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(batch_max_items=2, flush_interval_ms=1000),
    )
    sender = service.build_port()
    sender.send("worker:alpha#1", "a")
    sender.send("worker:alpha#1", "b")
    first = receiver.recv(timeout=0.1)
    second = receiver.recv(timeout=0.1)
    assert first is not None
    assert second is not None
    assert first.payload == "a"
    assert second.payload == "b"


def test_ipc_transport_batch_flushes_on_timer() -> None:
    service = InMemoryExecutionIpcTransportAdapter()
    receiver = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(batch_max_items=10, flush_interval_ms=10),
    )
    sender = service.build_port()
    sender.send("worker:alpha#1", "a")
    time.sleep(0.02)
    message = receiver.recv(timeout=0.1)
    assert message is not None
    assert message.payload == "a"


def test_ipc_transport_unbuffered_policy_flushes_singleton() -> None:
    service = InMemoryExecutionIpcTransportAdapter()
    receiver = service.build_port(
        target_id="worker:alpha#1",
        receive_policy=ExecutionIpcReceivePolicy(buffer_enabled=False, batch_max_items=10, flush_interval_ms=10),
    )
    sender = service.build_port()
    sender.send("worker:alpha#1", "a")
    sender.send("worker:alpha#1", "b")
    first = receiver.recv(timeout=0.1)
    second = receiver.recv(timeout=0.1)
    assert first is not None
    assert second is not None
    assert first.payload == "a"
    assert second.payload == "b"
