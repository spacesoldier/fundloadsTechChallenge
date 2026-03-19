from __future__ import annotations

import multiprocessing as mp
from threading import Event

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.lifecycle import (
    ExecutionWorkerHandle,
    LocalExecutionWorkerLifecycleService,
)


def _noop_worker(*_args: object, **_kwargs: object) -> None:
    return None


class _IpcTransport:
    def __init__(self, *, context: mp.context.BaseContext, endpoint_registry: InMemoryKvStore) -> None:
        self._context = context
        self._endpoint_registry = endpoint_registry

    def allocate_local_endpoints(
        self,
        target_id: str,
        *,
        register_parent_endpoint: bool = True,
    ) -> tuple[object, object]:
        _ = register_parent_endpoint
        parent, child = self._context.Pipe(duplex=True)
        self._endpoint_registry.set(target_id, parent)
        return parent, child


def test_worker_lifecycle_spawn_registers_endpoint_and_handle() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )

    handle = service.spawn_worker(
        target_id="group:alpha",
        target=_noop_worker,
        args=("hello",),
        start=False,
    )

    assert isinstance(handle, ExecutionWorkerHandle)
    stored_endpoint = endpoint_registry.get("group:alpha")
    assert stored_endpoint is not None
    assert callable(getattr(stored_endpoint, "send", None)) or callable(
        getattr(stored_endpoint, "send_bytes", None)
    )

    stored_handle = worker_registry.get("group:alpha")
    assert stored_handle is handle
    assert isinstance(handle.process._args, tuple)
    assert handle.process._args[-1] is not None


def test_worker_lifecycle_spawn_injects_stop_event_when_position_provided() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )
    event = Event()

    handle = service.spawn_worker(
        target_id="group:beta",
        target=_noop_worker,
        args=("hello",),
        start=False,
        stop_event=event,
        stop_event_position=0,
        child_endpoint_position=1,
    )

    assert isinstance(handle, ExecutionWorkerHandle)
    assert isinstance(handle.process._args, tuple)
    assert len(handle.process._args) >= 3
    stop_event = handle.process._args[0]
    child_endpoint = handle.process._args[1]
    assert stop_event is event
    assert isinstance(child_endpoint, dict)
    control_endpoint = child_endpoint.get(EXECUTION_IPC_LANE_CONTROL)
    assert callable(getattr(control_endpoint, "send", None)) or callable(
        getattr(control_endpoint, "send_bytes", None)
    )


def test_worker_lifecycle_resolve_endpoint_reads_registry() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )
    service.spawn_worker(
        target_id="group:gamma",
        target=_noop_worker,
        start=False,
    )
    endpoint = service.resolve_endpoint("group:gamma")
    assert endpoint is endpoint_registry.get("group:gamma")


def test_worker_lifecycle_stop_sets_stop_event() -> None:
    class _Event:
        def __init__(self) -> None:
            self.calls = 0

        def set(self) -> None:
            self.calls += 1

    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )
    event = _Event()
    handle = service.spawn_worker(
        target_id="group:delta",
        target=_noop_worker,
        start=False,
        stop_event=event,
        stop_event_position=0,
    )

    assert service.stop_worker(handle.target_id) is True
    assert event.calls == 1


def test_worker_lifecycle_stop_unknown_target_returns_false() -> None:
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=InMemoryKvStore(),
        execution_ipc=_IpcTransport(context=context, endpoint_registry=InMemoryKvStore()),
        context=context,
    )
    assert service.stop_worker("group:missing") is False


def test_worker_lifecycle_stop_releases_registry_and_resources() -> None:
    class _Process:
        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            _ = timeout

        def terminate(self) -> None:
            return None

    class _Closable:
        def __init__(self) -> None:
            self.closed = 0
            self.set_calls = 0

        def set(self) -> None:
            self.set_calls += 1

        def close(self) -> None:
            self.closed += 1

    context = mp.get_context("spawn")
    worker_registry = InMemoryKvStore()
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=InMemoryKvStore()),
        context=context,
    )
    stop_event = _Closable()
    control_parent = _Closable()
    handle = ExecutionWorkerHandle(
        target_id="group:cleanup",
        process=_Process(),  # type: ignore[arg-type]
        stop_event=stop_event,
        control_parent=control_parent,
    )
    worker_registry.set("group:cleanup", handle)

    assert service.stop_worker("group:cleanup") is True
    assert stop_event.set_calls == 1
    assert stop_event.closed == 1
    assert control_parent.closed == 1
    assert worker_registry.get("group:cleanup") is None


def test_worker_lifecycle_stop_terminates_process_after_graceful_timeout() -> None:
    class _Process:
        def __init__(self) -> None:
            self.alive = True
            self.join_calls: list[float | None] = []
            self.terminate_calls = 0
            self.kill_calls = 0

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.alive = False

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

    class _Closable:
        def __init__(self) -> None:
            self.closed = 0
            self.set_calls = 0

        def set(self) -> None:
            self.set_calls += 1

        def close(self) -> None:
            self.closed += 1

    context = mp.get_context("spawn")
    worker_registry = InMemoryKvStore()
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=InMemoryKvStore()),
        context=context,
    )
    process = _Process()
    stop_event = _Closable()
    control_parent = _Closable()
    worker_registry.set(
        "group:terminate",
        ExecutionWorkerHandle(
            target_id="group:terminate",
            process=process,  # type: ignore[arg-type]
            stop_event=stop_event,
            control_parent=control_parent,
        ),
    )

    assert service.stop_worker("group:terminate", graceful_timeout_seconds=0.01, terminate_timeout_seconds=0.2) is True
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert stop_event.closed == 1
    assert control_parent.closed == 1
    assert worker_registry.get("group:terminate") is None


def test_worker_lifecycle_stop_kills_process_when_terminate_is_insufficient() -> None:
    class _Process:
        def __init__(self) -> None:
            self.alive = True
            self.join_calls: list[float | None] = []
            self.terminate_calls = 0
            self.kill_calls = 0

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

    class _Closable:
        def __init__(self) -> None:
            self.closed = 0
            self.set_calls = 0

        def set(self) -> None:
            self.set_calls += 1

        def close(self) -> None:
            self.closed += 1

    context = mp.get_context("spawn")
    worker_registry = InMemoryKvStore()
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=InMemoryKvStore()),
        context=context,
    )
    process = _Process()
    stop_event = _Closable()
    control_parent = _Closable()
    worker_registry.set(
        "group:kill",
        ExecutionWorkerHandle(
            target_id="group:kill",
            process=process,  # type: ignore[arg-type]
            stop_event=stop_event,
            control_parent=control_parent,
        ),
    )

    assert service.stop_worker("group:kill", graceful_timeout_seconds=0.01, terminate_timeout_seconds=0.2) is True
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert stop_event.closed == 1
    assert control_parent.closed == 1
    assert worker_registry.get("group:kill") is None


def test_worker_lifecycle_insert_arg_preserves_position_for_none_placeholder() -> None:
    args = ["bundle", "worker_id"]

    shifted = LocalExecutionWorkerLifecycleService._insert_arg(args, 0, None)  # type: ignore[attr-defined]

    assert shifted[0] is None
    assert shifted[1:] == ["bundle", "worker_id"]


def test_worker_lifecycle_spawn_merges_extra_child_endpoints() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )
    custom_endpoint = object()

    handle = service.spawn_worker(
        target_id="group:ring",
        target=_noop_worker,
        start=False,
        extra_child_endpoints={"target::ring:group:ring->group:beta:data": custom_endpoint},
    )

    assert isinstance(handle.process._args, tuple)
    child_endpoint = handle.process._args[-1]
    assert isinstance(child_endpoint, dict)
    assert child_endpoint["target::ring:group:ring->group:beta:data"] is custom_endpoint


def test_worker_lifecycle_spawn_allows_lane_subset_for_endpoint_allocation() -> None:
    endpoint_registry = InMemoryKvStore()
    worker_registry = InMemoryKvStore()
    context = mp.get_context("spawn")
    service = LocalExecutionWorkerLifecycleService(
        worker_registry=worker_registry,
        execution_ipc=_IpcTransport(context=context, endpoint_registry=endpoint_registry),
        context=context,
    )

    handle = service.spawn_worker(
        target_id="group:subset",
        target=_noop_worker,
        start=False,
        lane_names=(EXECUTION_IPC_LANE_CONTROL, EXECUTION_IPC_LANE_DATA),
    )

    assert isinstance(handle.process._args, tuple)
    child_endpoint = handle.process._args[-1]
    assert isinstance(child_endpoint, dict)
    assert set(child_endpoint.keys()) == {EXECUTION_IPC_LANE_CONTROL, EXECUTION_IPC_LANE_DATA}
    assert endpoint_registry.get("group:subset") is not None
    assert endpoint_registry.get(compose_execution_ipc_worker_target_id("group:subset", lane=EXECUTION_IPC_LANE_DATA)) is not None
    assert endpoint_registry.get(compose_execution_ipc_worker_target_id("group:subset", lane="trace")) is None
    assert endpoint_registry.get(compose_execution_ipc_worker_target_id("group:subset", lane="log")) is None
    assert endpoint_registry.get(compose_execution_ipc_worker_target_id("group:subset", lane="metric")) is None
