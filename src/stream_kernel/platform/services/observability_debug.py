from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.observability.events import DebugDispatchEvent


@runtime_checkable
class RuntimeDebugSinkPort(Protocol):
    def emit_async(self, message: DebugMessage) -> object:
        raise NotImplementedError


@runtime_checkable
class RuntimeDebugDispatchService(Protocol):
    # System dispatch service for runtime debug events.
    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        raise NotImplementedError


@service(name="runtime_debug_dispatch_service")
@dataclass(slots=True)
class DefaultRuntimeDebugDispatchService(RuntimeDebugDispatchService):
    sink: RuntimeDebugSinkPort = inject.stream(DebugMessage)

    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        if not isinstance(event, DebugDispatchEvent):
            return []
        message = event.payload
        if not isinstance(message, DebugMessage):
            return []
        try:
            result = self.sink.emit_async(message)
        except Exception:
            return []
        if inspect.isawaitable(result):
            return [result]
        return []


@dataclass(slots=True)
class NoOpRuntimeDebugDispatchService(RuntimeDebugDispatchService):
    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        _ = event
        return []


__all__ = [
    "RuntimeDebugSinkPort",
    "RuntimeDebugDispatchService",
    "DefaultRuntimeDebugDispatchService",
    "NoOpRuntimeDebugDispatchService",
]
