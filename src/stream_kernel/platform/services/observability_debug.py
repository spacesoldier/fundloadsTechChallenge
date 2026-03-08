from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.observability.events import DebugDispatchEvent


@runtime_checkable
class RuntimeDebugDispatchService(Protocol):
    # System dispatch service for runtime debug events.
    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        raise NotImplementedError


@service(name="runtime_debug_dispatch_service")
@dataclass(slots=True)
class DefaultRuntimeDebugDispatchService(RuntimeDebugDispatchService):
    sink: object = inject.stream(DebugMessage)

    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        if not isinstance(event, DebugDispatchEvent):
            return []
        message = event.payload
        if not isinstance(message, DebugMessage):
            return []
        emit_async = getattr(self.sink, "emit_async", None)
        if callable(emit_async):
            try:
                result = emit_async(message)
                if inspect.isawaitable(result):
                    return [result]
            except Exception:
                return []
            return []
        emit = getattr(self.sink, "emit", None)
        if callable(emit):
            try:
                emit(message)
            except Exception:
                return []
        return []


@dataclass(slots=True)
class NoOpRuntimeDebugDispatchService(RuntimeDebugDispatchService):
    def dispatch(self, *, event: DebugDispatchEvent) -> list[object]:
        _ = event
        return []


__all__ = [
    "RuntimeDebugDispatchService",
    "DefaultRuntimeDebugDispatchService",
    "NoOpRuntimeDebugDispatchService",
]
