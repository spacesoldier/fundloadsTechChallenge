from __future__ import annotations

from datetime import UTC, datetime

from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_message_dispatch import (
    DefaultRuntimeDebugMessageDispatchService,
)


def _message() -> DebugMessage:
    return DebugMessage(
        timestamp=datetime.now(tz=UTC),
        event="runtime.debug.test",
        source="tests.runtime.debug_dispatch",
        fields={"k": "v"},
    )


def test_runtime_debug_dispatch_service_calls_sink_and_returns_awaitable() -> None:
    class _Sink:
        def __init__(self) -> None:
            self.seen: list[DebugMessage] = []

        async def emit_async(self, message: DebugMessage) -> None:
            self.seen.append(message)

    sink = _Sink()
    service = DefaultRuntimeDebugMessageDispatchService(sink=sink)

    outputs = service.dispatch(message=_message())

    assert len(outputs) == 1
    assert sink.seen == []


def test_runtime_debug_dispatch_service_ignores_non_debug_payload() -> None:
    class _Sink:
        def emit_async(self, _message: DebugMessage) -> None:
            raise AssertionError("sink must not be called for non-debug payload")

    service = DefaultRuntimeDebugMessageDispatchService(sink=_Sink())
    outputs = service.dispatch(message="not-a-debug-message")  # type: ignore[arg-type]

    assert outputs == []

