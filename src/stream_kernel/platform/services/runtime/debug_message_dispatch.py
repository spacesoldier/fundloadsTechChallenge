from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage


@runtime_checkable
class RuntimeDebugMessageSinkPort(Protocol):
    def emit_async(self, message: DebugMessage) -> object:
        raise NotImplementedError


@runtime_checkable
class RuntimeDebugMessageDispatchService(Protocol):
    def dispatch(self, *, message: DebugMessage) -> list[object]:
        raise NotImplementedError


@service(name="runtime_debug_message_dispatch_service")
@dataclass(slots=True)
class DefaultRuntimeDebugMessageDispatchService(RuntimeDebugMessageDispatchService):
    sink: RuntimeDebugMessageSinkPort = inject.stream(DebugMessage)

    def dispatch(self, *, message: DebugMessage) -> list[object]:
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
class NoOpRuntimeDebugMessageDispatchService(RuntimeDebugMessageDispatchService):
    def dispatch(self, *, message: DebugMessage) -> list[object]:
        _ = message
        return []


__all__ = [
    "RuntimeDebugMessageSinkPort",
    "RuntimeDebugMessageDispatchService",
    "DefaultRuntimeDebugMessageDispatchService",
    "NoOpRuntimeDebugMessageDispatchService",
]
