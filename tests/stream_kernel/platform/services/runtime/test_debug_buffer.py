from __future__ import annotations

from datetime import UTC, datetime

from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_buffer import InMemoryRuntimeDebugBufferService


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

