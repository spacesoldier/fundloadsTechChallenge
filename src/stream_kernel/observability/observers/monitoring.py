from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol

from stream_kernel.execution.observers.observer import (
    ExecutionObserver,
    ObserverFactoryContext,
    observer_factory,
)
from stream_kernel.observability.domain.monitoring import MonitoringMessage


class MonitoringSinkLike(Protocol):
    def emit(self, message: MonitoringMessage) -> None: ...
    def close(self) -> None: ...


def _coerce_monitoring_message(event: object) -> MonitoringMessage:
    if isinstance(event, MonitoringMessage):
        return event
    return MonitoringMessage(
        name="monitoring_event",
        status="accepted",
        details={"value_type": type(event).__name__},
    )


class _FanoutMonitoringSink:
    def __init__(self, sinks: list[MonitoringSinkLike]) -> None:
        self._sinks = list(sinks)

    def emit(self, message: MonitoringMessage) -> None:
        for sink in self._sinks:
            emit = getattr(sink, "emit", None)
            if not callable(emit):
                continue
            try:
                emit(message)
            except Exception:
                continue

    async def emit_async(self, message: MonitoringMessage) -> None:
        for sink in self._sinks:
            emit_async = getattr(sink, "emit_async", None)
            if callable(emit_async):
                try:
                    result = emit_async(message)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    continue
                continue
            emit = getattr(sink, "emit", None)
            if callable(emit):
                try:
                    emit(message)
                except Exception:
                    continue

    def close(self) -> None:
        for sink in self._sinks:
            close = getattr(sink, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                continue


@dataclass(slots=True)
class MonitoringDispatchObserver(ExecutionObserver):
    sink: _FanoutMonitoringSink

    def on_monitoring_event(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        _ = (trace_id, attributes)
        self.sink.emit(_coerce_monitoring_message(event))

    async def on_monitoring_event_async(
        self,
        *,
        event: object,
        trace_id: str | None,
        attributes: dict[str, object] | None,
    ) -> None:
        _ = (trace_id, attributes)
        await self.sink.emit_async(_coerce_monitoring_message(event))

    def on_run_end(self) -> None:
        self.sink.close()


def _collect_monitoring_sinks(adapter_instances: dict[str, object]) -> list[MonitoringSinkLike]:
    sinks: list[MonitoringSinkLike] = []
    seen_ids: set[int] = set()
    for key, candidate in adapter_instances.items():
        if not isinstance(key, str) or not key.startswith("monitoring_"):
            continue
        if not callable(getattr(candidate, "emit", None)):
            continue
        marker = id(candidate)
        if marker in seen_ids:
            continue
        seen_ids.add(marker)
        sinks.append(candidate)  # type: ignore[arg-type]
    return sinks


@observer_factory(name="monitoring_dispatch")
def build_monitoring_dispatch_observer(ctx: ObserverFactoryContext) -> ExecutionObserver | None:
    sinks = _collect_monitoring_sinks(ctx.adapter_instances)
    if not sinks:
        return None
    return MonitoringDispatchObserver(sink=_FanoutMonitoringSink(sinks))
