from __future__ import annotations

import multiprocessing as mp
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcTransportService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore

from .models import ExecutionWorkerHandle, ExecutionWorkerRegistry


@runtime_checkable
class ExecutionWorkerLifecycleService(Protocol):
    # Platform lifecycle service for worker processes.
    def spawn_worker(
        self,
        *,
        target_id: str,
        target: Callable[..., object],
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        name: str | None = None,
        daemon: bool = True,
        start: bool = True,
        stop_event: object | None = None,
        stop_event_position: int | None = None,
        child_endpoint_position: int | None = None,
        close_child_in_parent: bool = True,
    ) -> ExecutionWorkerHandle:
        raise NotImplementedError("ExecutionWorkerLifecycleService.spawn_worker must be implemented")

    def resolve_worker(self, target_id: str) -> ExecutionWorkerHandle | None:
        raise NotImplementedError("ExecutionWorkerLifecycleService.resolve_worker must be implemented")

    def resolve_endpoint(self, target_id: str) -> object | None:
        raise NotImplementedError("ExecutionWorkerLifecycleService.resolve_endpoint must be implemented")

    def stop_worker(
        self,
        target_id: str,
        *,
        graceful_timeout_seconds: float = 0.0,
        terminate_timeout_seconds: float = 1.0,
    ) -> bool:
        raise NotImplementedError("ExecutionWorkerLifecycleService.stop_worker must be implemented")

    def snapshot(self) -> dict[str, dict[str, object]]:
        raise NotImplementedError("ExecutionWorkerLifecycleService.snapshot must be implemented")


@service(name="execution_worker_lifecycle")
@dataclass(slots=True)
class LocalExecutionWorkerLifecycleService(ExecutionWorkerLifecycleService):
    worker_registry: object = inject.kv(ExecutionWorkerRegistry)
    execution_ipc: object = inject.service(ExecutionIpcTransportService)
    context: mp.context.BaseContext = field(default_factory=lambda: mp.get_context("spawn"))

    def spawn_worker(
        self,
        *,
        target_id: str,
        target: Callable[..., object],
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        name: str | None = None,
        daemon: bool = True,
        start: bool = True,
        stop_event: object | None = None,
        stop_event_position: int | None = None,
        child_endpoint_position: int | None = None,
        close_child_in_parent: bool = True,
    ) -> ExecutionWorkerHandle:
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("ExecutionWorkerLifecycleService.spawn_worker requires non-empty target_id")
        if not callable(target):
            raise ValueError("ExecutionWorkerLifecycleService.spawn_worker requires callable target")
        parent_conn, child_conn = self._ipc().allocate_local_endpoints(target_id)
        if stop_event is None and stop_event_position is not None:
            try:
                stop_event = self.context.Event()
            except (PermissionError, OSError, RuntimeError):
                stop_event = None
        args_list = list(args)
        args_list = self._insert_arg(args_list, stop_event_position, stop_event)
        args_list = self._insert_arg(args_list, child_endpoint_position, child_conn)
        process = self.context.Process(
            target=target,
            args=tuple(args_list),
            kwargs=kwargs or {},
            name=name,
            daemon=daemon,
        )
        if start:
            process.start()
            if close_child_in_parent:
                _close_pipe(child_conn)
        handle = ExecutionWorkerHandle(
            target_id=target_id,
            process=process,
            stop_event=stop_event,
            control_parent=parent_conn,
        )
        self._worker_store().set(target_id, handle)
        return handle

    def resolve_worker(self, target_id: str) -> ExecutionWorkerHandle | None:
        if not isinstance(target_id, str) or not target_id:
            return None
        candidate = self._worker_store().get(target_id)
        if isinstance(candidate, ExecutionWorkerHandle):
            return candidate
        return None

    def resolve_endpoint(self, target_id: str) -> object | None:
        handle = self.resolve_worker(target_id)
        if handle is None:
            return None
        return handle.control_parent

    def stop_worker(
        self,
        target_id: str,
        *,
        graceful_timeout_seconds: float = 0.0,
        terminate_timeout_seconds: float = 1.0,
    ) -> bool:
        handle = self.resolve_worker(target_id)
        if handle is None:
            return False
        process = handle.process
        try:
            if handle.stop_event is not None:
                setter = getattr(handle.stop_event, "set", None)
                if callable(setter):
                    setter()
            if process.is_alive():
                process.join(timeout=max(0.0, float(graceful_timeout_seconds)))
            if process.is_alive():
                process.terminate()
                process.join(timeout=max(0.0, float(terminate_timeout_seconds)))
            return True
        finally:
            # Always release process-local resources even when stop/join fails.
            _close_pipe(handle.control_parent)
            _close_optional(handle.stop_event)
            self._worker_store().delete(target_id)

    def snapshot(self) -> dict[str, dict[str, object]]:
        snapshot: dict[str, dict[str, object]] = {}
        store = self._worker_store()
        if not isinstance(store, InMemoryKvStore):
            return snapshot
        for target_id, handle in store._store.items():  # noqa: SLF001 - internal snapshot probe
            if not isinstance(handle, ExecutionWorkerHandle):
                continue
            process = handle.process
            snapshot[target_id] = {
                "pid": process.pid,
                "alive": process.is_alive(),
                "daemon": process.daemon,
            }
        return snapshot

    def _worker_store(self) -> KVStore:
        if not isinstance(self.worker_registry, KVStore):
            raise ValueError("ExecutionWorkerRegistry binding must resolve to KVStore")
        return self.worker_registry

    def _ipc(self) -> ExecutionIpcTransportService:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "allocate_local_endpoints", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcTransportService binding is required")

    @staticmethod
    def _insert_arg(
        args_list: list[object],
        position: int | None,
        value: object | None,
    ) -> list[object]:
        if value is None and position is None:
            return args_list
        if position is None:
            args_list.append(value)
            return args_list
        index = max(0, min(int(position), len(args_list)))
        args_list.insert(index, value)
        return args_list


def _close_pipe(pipe: object) -> None:
    close = getattr(pipe, "close", None)
    if callable(close):
        close()


def _close_optional(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        close()


__all__ = [
    "ExecutionWorkerLifecycleService",
    "LocalExecutionWorkerLifecycleService",
]
