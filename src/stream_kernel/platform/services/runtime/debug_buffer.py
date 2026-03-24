from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage


@runtime_checkable
class RuntimeDebugBufferService(Protocol):
    # Per-process runtime debug buffer consumed by runner and routed as DebugDispatchEvent.
    def publish(self, message: DebugMessage) -> None:
        raise NotImplementedError

    def drain(self, *, max_items: int = 256) -> list[DebugMessage]:
        raise NotImplementedError


_DIRECT_BATCH_SIZE = 100


@service(name="runtime_debug_buffer_service")
@dataclass(slots=True)
class InMemoryRuntimeDebugBufferService(RuntimeDebugBufferService):
    debug_stream: object | None = inject.stream(DebugMessage)
    _items: deque[DebugMessage] = field(default_factory=deque)
    _lock: Lock = field(default_factory=Lock)
    _direct_batch: list[DebugMessage] = field(default_factory=list, init=False, repr=False)

    def publish(self, message: DebugMessage) -> None:
        if not isinstance(message, DebugMessage):
            return
        if _runtime_debug_direct_dispatch_enabled():
            batch: list[DebugMessage] | None = None
            with self._lock:
                self._direct_batch.append(message)
                if len(self._direct_batch) >= _DIRECT_BATCH_SIZE:
                    batch = self._direct_batch[:]
                    self._direct_batch.clear()
            if batch is not None:
                self._emit_direct_batch(batch)
            return
        with self._lock:
            self._items.append(message)

    def drain(self, *, max_items: int = 256) -> list[DebugMessage]:
        limit = max(1, int(max_items))
        drained: list[DebugMessage] = []
        pending_direct: list[DebugMessage] = []
        with self._lock:
            if self._direct_batch:
                pending_direct = self._direct_batch[:]
                self._direct_batch.clear()
            while self._items and len(drained) < limit:
                drained.append(self._items.popleft())
        # Flush the pending direct batch; any messages that fail to emit are
        # written back into _items by _emit_direct_batch.
        if pending_direct:
            self._emit_direct_batch(pending_direct)
        # Second pass: pick up any fallback items written by _emit_direct_batch.
        if len(drained) < limit:
            with self._lock:
                while self._items and len(drained) < limit:
                    drained.append(self._items.popleft())
        return drained

    def _emit_direct_batch(self, messages: list[DebugMessage]) -> None:
        sink = self.debug_stream
        emit_batch = getattr(sink, "emit_batch", None)
        if callable(emit_batch):
            try:
                emit_batch(messages)
                return
            except Exception:
                pass
        # Fallback: individual emit. Messages that fail go back to _items so
        # they are still retrievable via drain() (same contract as the old
        # single-message _emit_direct fallback path).
        emit = getattr(sink, "emit", None)
        failed: list[DebugMessage] = []
        if callable(emit):
            for msg in messages:
                try:
                    emit(msg)
                except Exception:
                    failed.append(msg)
        else:
            failed = list(messages)
        if failed:
            with self._lock:
                self._items.extend(failed)

    def _emit_direct(self, message: DebugMessage) -> bool:
        sink = self.debug_stream
        emit = getattr(sink, "emit", None)
        if callable(emit):
            try:
                emit(message)
            except Exception:
                return False
            return True
        emit_async = getattr(sink, "emit_async", None)
        if not callable(emit_async):
            return False
        try:
            result = emit_async(message)
        except Exception:
            return False
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    asyncio.run(result)
                except Exception:
                    return False
                return True
            try:
                loop.create_task(result)
            except Exception:
                return False
            return True
        return True


def runtime_debug_enabled() -> bool:
    raw = os.getenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_debug_direct_dispatch_enabled() -> bool:
    raw = os.getenv("STREAM_KERNEL_RUNTIME_DEBUG_DIRECT_DISPATCH", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def publish_runtime_debug(
    *,
    buffer: object,
    event: str,
    source: str,
    fields: dict[str, object] | None = None,
    trace_id: str | None = None,
    force: bool = False,
) -> None:
    if not force and not runtime_debug_enabled():
        return
    normalized_fields = dict(fields or {})
    if _is_runtime_debug_scheduler_noise(
        event=event,
        source=source,
        fields=normalized_fields,
    ):
        return
    publish = getattr(buffer, "publish", None)
    if not callable(publish):
        return
    try:
        publish(
            DebugMessage(
                timestamp=datetime.now(tz=UTC),
                event=event,
                source=source,
                fields=normalized_fields,
                run_id=_env("STREAM_KERNEL_LOGICAL_RUN_ID"),
                run_instance_id=_env("STREAM_KERNEL_RUN_INSTANCE_ID"),
                process_group=_env("STREAM_KERNEL_PROCESS_GROUP"),
                worker_id=_env("STREAM_KERNEL_WORKER_ID"),
                trace_id=trace_id,
            )
        )
    except Exception:
        return


def debug_instrument_service_methods(cls: type[Any]) -> type[Any]:
    # Class decorator: wraps public service methods and publishes DebugMessage records.
    for name, attr in list(cls.__dict__.items()):
        if name.startswith("_"):
            continue
        if isinstance(attr, staticmethod) or isinstance(attr, classmethod):
            continue
        if not callable(attr):
            continue
        setattr(cls, name, _wrap_service_method(cls=cls, method_name=name, func=attr))
    return cls


def _wrap_service_method(*, cls: type[Any], method_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(func):

        async def _wrapped(self: object, *args: object, **kwargs: object) -> Any:
            started = time.perf_counter()
            try:
                result = await func(self, *args, **kwargs)
            except Exception as exc:
                try:
                    _emit_service_call_debug(
                        owner=self,
                        owner_cls=cls,
                        method_name=method_name,
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        args=args,
                        kwargs=kwargs,
                        error=exc,
                    )
                except Exception:
                    pass
                raise
            try:
                _emit_service_call_debug(
                    owner=self,
                    owner_cls=cls,
                    method_name=method_name,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    args=args,
                    kwargs=kwargs,
                    error=None,
                    result=result,
                )
            except Exception:
                pass
            return result

        _wrapped.__name__ = func.__name__
        _wrapped.__qualname__ = func.__qualname__
        _wrapped.__doc__ = func.__doc__
        return _wrapped

    def _wrapped(self: object, *args: object, **kwargs: object) -> Any:
        started = time.perf_counter()
        try:
            result = func(self, *args, **kwargs)
        except Exception as exc:
            try:
                _emit_service_call_debug(
                    owner=self,
                    owner_cls=cls,
                    method_name=method_name,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    args=args,
                    kwargs=kwargs,
                    error=exc,
                )
            except Exception:
                pass
            raise
        try:
            _emit_service_call_debug(
                owner=self,
                owner_cls=cls,
                method_name=method_name,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                args=args,
                kwargs=kwargs,
                error=None,
                result=result,
            )
        except Exception:
            pass
        return result

    _wrapped.__name__ = func.__name__
    _wrapped.__qualname__ = func.__qualname__
    _wrapped.__doc__ = func.__doc__
    return _wrapped


def _emit_service_call_debug(
    *,
    owner: object,
    owner_cls: type[Any],
    method_name: str,
    duration_ms: float,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error: Exception | None,
    result: object | None = None,
) -> None:
    if not runtime_debug_enabled():
        return
    buffer = getattr(owner, "runtime_debug_buffer", None)
    if buffer is None:
        return
    # Intentionally omit recursive arg/result serialization and frame inspection
    # here — those operations (build_call_payload_fields, _resolve_caller) are
    # expensive on the hot asyncio event-loop path (frame walks + deep object
    # traversal of binary IPC frames).  Basic timing/status fields are sufficient
    # for runtime diagnostics; payload detail can be added per call-site if needed.
    fields: dict[str, object] = {
        "service_type": owner_cls.__name__,
        "service_module": owner_cls.__module__,
        "method": method_name,
        "duration_ms": round(max(0.0, float(duration_ms)), 3),
        "args_count": len(args),
        "status": "error" if isinstance(error, Exception) else "ok",
    }
    if isinstance(error, Exception):
        fields["error_type"] = type(error).__name__
        fields["error_message"] = str(error)
    publish_runtime_debug(
        buffer=buffer,
        event="runtime.service.call",
        source=f"{owner_cls.__module__}.{owner_cls.__name__}",
        fields=fields,
        trace_id=None,
    )


def _is_runtime_debug_scheduler_noise(
    *,
    event: str,
    source: str,
    fields: dict[str, object],
) -> bool:
    source_node = fields.get("source_node")
    target = fields.get("target")
    payload_model = fields.get("payload_model")
    if event in {"runtime.runner.dequeued", "runtime.runner.enqueued"}:
        if source_node == "system.scheduler.tick":
            return True
        if isinstance(target, str) and (
            target == "system.scheduler.tick"
            or target.startswith("source:system.cp.root_leaf_ingress:")
            or target.startswith("source:system.cp.command_ingress:")
            or target.startswith("source:system.ipc.ingress:")
        ):
            if payload_model in {"BootstrapControl", "PlatformSchedulerTickEvent"}:
                return True
    if event == "runtime.service.call":
        service_module = fields.get("service_module")
        method = fields.get("method")
        if (
            isinstance(service_module, str)
            and service_module == "stream_kernel.platform.services.runtime.platform_scheduler"
            and method in {"dispatch_due", "apply_command", "snapshot"}
        ):
            return True
        if isinstance(source, str) and source.startswith(
            "stream_kernel.platform.services.runtime.platform_scheduler."
        ):
            if method in {"dispatch_due", "apply_command", "snapshot"}:
                return True
    return False



def _env(name: str) -> str | None:
    value = os.getenv(name)
    if isinstance(value, str) and value:
        return value
    return None


__all__ = [
    "RuntimeDebugBufferService",
    "InMemoryRuntimeDebugBufferService",
    "runtime_debug_enabled",
    "publish_runtime_debug",
    "debug_instrument_service_methods",
]
