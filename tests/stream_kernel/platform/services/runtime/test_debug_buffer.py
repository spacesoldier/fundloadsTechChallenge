from __future__ import annotations

from datetime import UTC, datetime

from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_buffer import (
    InMemoryRuntimeDebugBufferService,
    debug_instrument_service_methods,
    publish_runtime_debug,
)


def _message() -> DebugMessage:
    return DebugMessage(
        timestamp=datetime.now(tz=UTC),
        event="runtime.test",
        source="tests",
        fields={},
        run_id="run",
        run_instance_id="run-instance",
        process_group="execution.ingress",
        worker_id="execution.ingress#1",
        trace_id=None,
    )


def test_runtime_debug_buffer_direct_dispatch_skips_buffer(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_RUNTIME_DEBUG_DIRECT_DISPATCH", "1")

    class _Sink:
        def __init__(self) -> None:
            self.items: list[DebugMessage] = []

        def emit(self, message: DebugMessage) -> None:
            self.items.append(message)

    sink = _Sink()
    service = InMemoryRuntimeDebugBufferService(debug_stream=sink)

    service.publish(_message())

    # Messages are batched; drain() flushes the pending direct batch to sink.
    drained = service.drain(max_items=16)
    assert len(sink.items) == 1
    assert drained == []  # nothing went into the internal _items buffer


def test_runtime_debug_buffer_direct_dispatch_falls_back_to_buffer_on_error(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_RUNTIME_DEBUG_DIRECT_DISPATCH", "1")

    class _Sink:
        def emit(self, message: DebugMessage) -> None:
            _ = message
            raise RuntimeError("down")

    service = InMemoryRuntimeDebugBufferService(debug_stream=_Sink())
    message = _message()

    service.publish(message)

    drained = service.drain(max_items=16)
    assert len(drained) == 1
    assert drained[0] == message


def test_debug_instrument_service_methods_emits_payload_fields(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")

    class _Buffer:
        def __init__(self) -> None:
            self.items: list[DebugMessage] = []

        def publish(self, message: DebugMessage) -> None:
            self.items.append(message)

    @debug_instrument_service_methods
    class _Service:
        runtime_debug_buffer: object

        def process(self, payload: object) -> object:
            return payload

    buffer = _Buffer()
    service = _Service()
    service.runtime_debug_buffer = buffer

    result = service.process({"id": 42, "kind": "demo"})

    assert result == {"id": 42, "kind": "demo"}
    assert len(buffer.items) == 1
    message = buffer.items[0]
    assert message.event == "runtime.service.call"
    assert message.fields.get("method") == "process"
    assert message.fields.get("status") == "ok"
    assert message.fields.get("args_count") == 1
    # Payload serialization is intentionally omitted from the hot-path decorator
    # to avoid blocking the event loop with recursive object traversal.


def test_publish_runtime_debug_skips_scheduler_tick_noise(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")

    class _Buffer:
        def __init__(self) -> None:
            self.items: list[DebugMessage] = []

        def publish(self, message: DebugMessage) -> None:
            self.items.append(message)

    buffer = _Buffer()
    publish_runtime_debug(
        buffer=buffer,
        event="runtime.runner.dequeued",
        source="stream_kernel.execution.runtime.runner",
        fields={
            "source_node": "system.scheduler.tick",
            "target": "source:system.cp.root_leaf_ingress:execution.ingress#1:control",
            "payload_model": "PlatformSchedulerTickEvent",
        },
        trace_id="trace-x",
    )
    assert buffer.items == []


def test_publish_runtime_debug_keeps_non_scheduler_runner_events(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")

    class _Buffer:
        def __init__(self) -> None:
            self.items: list[DebugMessage] = []

        def publish(self, message: DebugMessage) -> None:
            self.items.append(message)

    buffer = _Buffer()
    publish_runtime_debug(
        buffer=buffer,
        event="runtime.runner.dequeued",
        source="stream_kernel.execution.runtime.runner",
        fields={
            "source_node": "ingress.node",
            "target": "transform.node",
            "payload_model": "InputRecord",
        },
        trace_id="trace-y",
    )
    assert len(buffer.items) == 1


def test_publish_runtime_debug_skips_scheduler_service_call_noise(monkeypatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")

    class _Buffer:
        def __init__(self) -> None:
            self.items: list[DebugMessage] = []

        def publish(self, message: DebugMessage) -> None:
            self.items.append(message)

    buffer = _Buffer()
    publish_runtime_debug(
        buffer=buffer,
        event="runtime.service.call",
        source="stream_kernel.platform.services.runtime.platform_scheduler.InMemoryPlatformSchedulerService",
        fields={
            "service_module": "stream_kernel.platform.services.runtime.platform_scheduler",
            "method": "dispatch_due",
            "status": "ok",
        },
        trace_id=None,
    )
    assert buffer.items == []
