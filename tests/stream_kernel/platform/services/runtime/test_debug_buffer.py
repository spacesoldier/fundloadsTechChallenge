from __future__ import annotations

from datetime import UTC, datetime

from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_buffer import (
    InMemoryRuntimeDebugBufferService,
    debug_instrument_service_methods,
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

    assert len(sink.items) == 1
    assert service.drain(max_items=16) == []


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
    assert message.fields.get("payload_model") == "dict"
    assert message.fields.get("payload") == {"id": 42, "kind": "demo"}
