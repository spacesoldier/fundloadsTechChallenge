from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service
from stream_kernel.observability.adapters.logging import StdoutPlainLogSink
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.async_dispatch_loop import AsyncDispatchLoop


@runtime_checkable
class RootConsoleLogDispatchService(Protocol):
    def publish(self, message: LogMessage) -> bool:
        raise NotImplementedError

    def drain(self, *, timeout_seconds: float = 1.0) -> bool:
        raise NotImplementedError

    def stop(self, *, drain: bool = True, timeout_seconds: float = 1.0) -> None:
        raise NotImplementedError


@service(name="root_console_log_dispatch_service")
@dataclass(slots=True)
class DefaultRootConsoleLogDispatchService(RootConsoleLogDispatchService):
    sink: object | None = None
    queue_max_items: int = 2048
    drop_policy: str = "block_with_timeout"
    block_timeout_seconds: float = 0.1
    _loop: AsyncDispatchLoop[LogMessage] | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def publish(self, message: LogMessage) -> bool:
        if not isinstance(message, LogMessage):
            raise ValueError("RootConsoleLogDispatchService.publish expects LogMessage")
        loop = self._ensure_loop()
        if not self._started:
            loop.start()
            self._started = True
        return bool(loop.submit(message, timeout_seconds=max(0.001, float(self.block_timeout_seconds))))

    def drain(self, *, timeout_seconds: float = 1.0) -> bool:
        if not self._started or self._loop is None:
            return True
        return bool(self._loop.drain(timeout_seconds=max(0.01, float(timeout_seconds))))

    def stop(self, *, drain: bool = True, timeout_seconds: float = 1.0) -> None:
        if self._loop is None:
            return
        self._loop.stop(drain=bool(drain), timeout_seconds=max(0.01, float(timeout_seconds)))
        self._started = False

    def _ensure_loop(self) -> AsyncDispatchLoop[LogMessage]:
        if self._loop is not None:
            return self._loop
        self._loop = AsyncDispatchLoop(
            name="root-console-log-dispatch",
            handler=self._handle_log_message,
            queue_max_items=max(1, int(self.queue_max_items)),
            drop_policy=str(self.drop_policy),
            block_timeout_seconds=max(0.001, float(self.block_timeout_seconds)),
        )
        return self._loop

    async def _handle_log_message(self, message: LogMessage) -> None:
        sink = self._sink()
        emit_async = getattr(sink, "emit_async", None)
        if callable(emit_async):
            await emit_async(message)
            return
        emit = getattr(sink, "emit", None)
        if callable(emit):
            emit(message)

    def _sink(self) -> object:
        if self.sink is None:
            self.sink = StdoutPlainLogSink()
        return self.sink


__all__ = [
    "RootConsoleLogDispatchService",
    "DefaultRootConsoleLogDispatchService",
]

