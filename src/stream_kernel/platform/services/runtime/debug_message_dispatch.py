from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage


@runtime_checkable
class RuntimeDebugMessageDispatchService(Protocol):
    def dispatch(self, *, message: DebugMessage) -> list[object]:
        raise NotImplementedError


@service(name="runtime_debug_message_dispatch_service")
@dataclass(slots=True)
class DefaultRuntimeDebugMessageDispatchService(RuntimeDebugMessageDispatchService):
    sink: object = inject.stream(DebugMessage)

    def dispatch(self, *, message: DebugMessage) -> list[object]:
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
class NoOpRuntimeDebugMessageDispatchService(RuntimeDebugMessageDispatchService):
    def dispatch(self, *, message: DebugMessage) -> list[object]:
        _ = message
        return []


__all__ = [
    "RuntimeDebugMessageDispatchService",
    "DefaultRuntimeDebugMessageDispatchService",
    "NoOpRuntimeDebugMessageDispatchService",
]
