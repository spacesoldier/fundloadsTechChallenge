from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass
from typing import Any

from stream_kernel.application_context.debug_payload import (
    build_call_payload_fields,
    build_result_payload_fields,
)
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.observability.domain.logging import LogMessage


@dataclass(frozen=True, slots=True)
class InjectPortDebugMeta:
    owner_type: str
    owner_module: str
    owner_field: str
    port_type: str
    port_data_type: str
    qualifier: str | None
    port_impl_type: str


class _InjectedPortDebugProxy:
    __slots__ = ("_target", "_meta", "_emit", "_wrapped")

    def __init__(
        self,
        *,
        target: object,
        meta: InjectPortDebugMeta,
        emit: Any,
    ) -> None:
        self._target = target
        self._meta = meta
        self._emit = emit
        self._wrapped: dict[str, object] = {}

    def __getattr__(self, name: str) -> object:
        target = self._target
        attr = getattr(target, name)
        if not callable(attr) or name.startswith("_"):
            return attr
        wrapped = self._wrapped.get(name)
        if wrapped is not None:
            return wrapped
        wrapped = self._build_callable(name=name, attr=attr)
        self._wrapped[name] = wrapped
        return wrapped

    @property
    def __class__(self) -> type[object]:
        # Preserve nominal/runtime protocol checks for wrapped dependencies.
        target_cls = getattr(self._target, "__class__", object)
        return target_cls if isinstance(target_cls, type) else object

    def __setattr__(self, name: str, value: object) -> None:
        if name in _InjectedPortDebugProxy.__slots__:
            object.__setattr__(self, name, value)
            return
        setattr(self._target, name, value)

    def __call__(self, *args: object, **kwargs: object) -> object:
        target = self._target
        if not callable(target):
            raise TypeError(f"{type(target).__name__!s} is not callable")
        method = self._build_callable(name="__call__", attr=target)
        return method(*args, **kwargs)  # type: ignore[misc]

    def _build_callable(self, *, name: str, attr: Any) -> Any:
        def _wrapped(*args: object, **kwargs: object) -> object:
            started_at = time.perf_counter()
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                if _should_emit_error_debug(method=name, error=exc):
                    try:
                        _emit_port_debug(
                            emit=self._emit,
                            meta=self._meta,
                            method=name,
                            status="error",
                            duration_ms=(time.perf_counter() - started_at) * 1000.0,
                            args=args,
                            kwargs=kwargs,
                            error=exc,
                        )
                    except Exception:
                        pass
                raise
            if inspect.isawaitable(result):
                return self._await_and_emit(
                    result=result,
                    method=name,
                    started_at=started_at,
                    args=args,
                    kwargs=kwargs,
                )
            if _should_emit_success_debug(method=name, result=result):
                try:
                    _emit_port_debug(
                        emit=self._emit,
                        meta=self._meta,
                        method=name,
                        status="ok",
                        duration_ms=(time.perf_counter() - started_at) * 1000.0,
                        args=args,
                        kwargs=kwargs,
                        error=None,
                        result=result,
                    )
                except Exception:
                    pass
            return result

        return _wrapped

    async def _await_and_emit(
        self,
        *,
        result: object,
        method: str,
        started_at: float,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> object:
        try:
            resolved = await result  # type: ignore[misc]
        except Exception as exc:
            if _should_emit_error_debug(method=method, error=exc):
                try:
                    _emit_port_debug(
                        emit=self._emit,
                        meta=self._meta,
                        method=method,
                        status="error",
                        duration_ms=(time.perf_counter() - started_at) * 1000.0,
                        args=args,
                        kwargs=kwargs,
                        error=exc,
                    )
                except Exception:
                    pass
            raise
        if _should_emit_success_debug(method=method, result=resolved):
            try:
                _emit_port_debug(
                    emit=self._emit,
                    meta=self._meta,
                    method=method,
                    status="ok",
                    duration_ms=(time.perf_counter() - started_at) * 1000.0,
                    args=args,
                    kwargs=kwargs,
                    error=None,
                    result=resolved,
                )
            except Exception:
                pass
        return resolved


def instrument_injected_dependency(
    *,
    resolved: object,
    injected: object,
    scope: object,
    owner: object,
    field_name: str,
) -> object:
    if not _inject_debug_enabled():
        return resolved
    if isinstance(resolved, _InjectedPortDebugProxy):
        return resolved
    port_type = getattr(injected, "port_type", None)
    if port_type not in {
        "stream",
        "kv_stream",
        "request",
        "response",
        "queue",
        "topic",
        "ipc",
        "service",
    }:
        return resolved
    data_type = getattr(injected, "data_type", None)
    if _should_skip_instrumentation(
        port_type=port_type,
        data_type=data_type,
        resolved=resolved,
    ):
        return resolved
    emit = _resolve_debug_emitter(scope)
    if not callable(getattr(emit, "publish", None)):
        return resolved
    meta = InjectPortDebugMeta(
        owner_type=type(owner).__name__,
        owner_module=type(owner).__module__,
        owner_field=str(field_name),
        port_type=str(port_type),
        port_data_type=getattr(data_type, "__name__", repr(data_type)),
        qualifier=getattr(injected, "qualifier", None),
        port_impl_type=type(resolved).__name__,
    )
    return _InjectedPortDebugProxy(target=resolved, meta=meta, emit=emit)


def _should_skip_instrumentation(
    *,
    port_type: object,
    data_type: object,
    resolved: object,
) -> bool:
    if data_type is LogMessage:
        return True
    if getattr(data_type, "__name__", "") == "LogMessage":
        return True
    if data_type is DebugMessage:
        return True
    if getattr(data_type, "__name__", "") == "DebugMessage":
        return True
    if port_type != "service":
        return False
    contract_name = getattr(data_type, "__name__", "")
    contract_module = getattr(data_type, "__module__", "")
    if isinstance(contract_name, str) and contract_name.startswith("RuntimeDebug"):
        return True
    if isinstance(contract_module, str) and "runtime.debug" in contract_module:
        return True
    impl_name = type(resolved).__name__
    impl_module = type(resolved).__module__
    if isinstance(impl_name, str) and impl_name.startswith("RuntimeDebug"):
        return True
    if isinstance(impl_module, str) and "runtime.debug" in impl_module:
        return True
    return False


def _resolve_debug_emitter(scope: object) -> Any | None:
    from stream_kernel.platform.services.runtime.debug_buffer import RuntimeDebugBufferService
    from stream_kernel.platform.services.observability import ObservabilityPipelineService

    resolve = getattr(scope, "resolve", None)
    if not callable(resolve):
        return None
    try:
        return resolve("service", RuntimeDebugBufferService)
    except Exception:
        pass
    try:
        pipeline = resolve("service", ObservabilityPipelineService)
    except Exception:
        return None
    if callable(getattr(pipeline, "emit_log_event", None)) or callable(getattr(pipeline, "publish_log", None)):
        return _ObservabilityPipelineDebugEmitter(pipeline=pipeline)
    return None


def _inject_debug_enabled() -> bool:
    raw = os.getenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _emit_port_debug(
    *,
    emit: Any,
    meta: InjectPortDebugMeta,
    method: str,
    status: str,
    duration_ms: float,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error: Exception | None,
    result: object | None = None,
) -> None:
    from stream_kernel.platform.services.runtime.debug_buffer import publish_runtime_debug

    try:
        caller_module, caller_function = _resolve_caller()
        fields: dict[str, object] = {
            "process_name": _resolve_process_name(),
            "owner_type": meta.owner_type,
            "owner_module": meta.owner_module,
            "owner_field": meta.owner_field,
            "port_type": meta.port_type,
            "port_data_type": meta.port_data_type,
            "port_impl_type": meta.port_impl_type,
            "qualifier": meta.qualifier,
            "method": method,
            "status": status,
            "duration_ms": round(max(0.0, float(duration_ms)), 3),
            "args_count": len(args),
            "kwargs_keys": sorted(str(key) for key in kwargs.keys())[:12],
            "arg_types": [type(value).__name__ for value in args[:8]],
            "caller_module": caller_module,
            "caller_function": caller_function,
        }
        fields.update(
            build_call_payload_fields(
                method=method,
                args=args,
                kwargs=kwargs,
            )
        )
        if not isinstance(error, Exception):
            fields.update(build_result_payload_fields(result=result))
        if isinstance(error, Exception):
            fields["error_type"] = type(error).__name__
            fields["error_message"] = str(error)
        publish_runtime_debug(
            buffer=emit,
            event="runtime.inject.port_call",
            source=f"{meta.owner_module}.{meta.owner_type}",
            fields=fields,
            trace_id=None,
        )
    except Exception:
        return


def _resolve_process_name() -> str:
    worker_id = os.getenv("STREAM_KERNEL_WORKER_ID")
    if isinstance(worker_id, str) and worker_id:
        return worker_id
    return "supervisor"


_POLL_METHOD_NAMES = {
    "recv",
    "receive",
    "poll",
    "read",
    "dequeue",
    "try_recv",
    "try_get",
    "get_nowait",
    "recv_nowait",
    "drain",
}


def _should_emit_success_debug(*, method: str, result: object) -> bool:
    if method not in _POLL_METHOD_NAMES:
        return True
    if result is None:
        return False
    if result is False:
        return False
    if isinstance(result, (int, float)) and result == 0:
        return False
    if isinstance(result, (str, bytes, bytearray, list, tuple, set, dict)) and len(result) == 0:
        return False
    return True


def _should_emit_error_debug(*, method: str, error: Exception) -> bool:
    if method not in _POLL_METHOD_NAMES:
        return True
    error_type = type(error).__name__
    if error_type in {"TimeoutError", "Empty", "WouldBlock"}:
        return False
    if isinstance(error, BlockingIOError):
        return False
    return True


def _resolve_caller() -> tuple[str | None, str | None]:
    frame = inspect.currentframe()
    if frame is None:
        return (None, None)
    caller = frame.f_back
    if caller is None:
        return (None, None)
    caller = caller.f_back
    if caller is None:
        return (None, None)
    module = caller.f_globals.get("__name__")
    function = caller.f_code.co_name
    return (
        module if isinstance(module, str) and module else None,
        function if isinstance(function, str) and function else None,
    )


@dataclass(slots=True)
class _ObservabilityPipelineDebugEmitter:
    pipeline: object

    def publish(self, message: object) -> None:
        if not isinstance(message, DebugMessage):
            return
        fields = dict(message.fields)
        fields["event"] = message.event
        fields.setdefault("source", message.source)
        fields.setdefault("debug_channel", "runtime_debug")
        if isinstance(message.run_instance_id, str) and message.run_instance_id:
            fields.setdefault("__run_instance_id", message.run_instance_id)
        if isinstance(message.run_id, str) and message.run_id:
            fields.setdefault("__run_id", message.run_id)
        if isinstance(message.process_group, str) and message.process_group:
            fields.setdefault("process_group", message.process_group)
        if isinstance(message.worker_id, str) and message.worker_id:
            fields.setdefault("worker_id", message.worker_id)
        log_message = LogMessage(
            level="debug",
            message=message.event,
            fields=fields,
        )
        emit_log_event = getattr(self.pipeline, "emit_log_event", None)
        if callable(emit_log_event):
            try:
                emit_log_event(
                    event=log_message,
                    trace_id=message.trace_id,
                    attributes={"channel": "runtime_debug"},
                )
            except Exception:
                return
            return
        publish_log = getattr(self.pipeline, "publish_log", None)
        if callable(publish_log):
            try:
                publish_log(
                    event=log_message,
                    trace_id=message.trace_id,
                    attributes={"channel": "runtime_debug"},
                )
            except Exception:
                return


__all__ = ["instrument_injected_dependency"]
