from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service import (
    DefaultControlPlaneLifecycleOrchestrationService,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.execution.transport.ipc.ipc_transport import (
    ExecutionIpcEndpointRegistry,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.lifecycle import ExecutionWorkerHandle


@dataclass(slots=True)
class _WorkerLifecycle:
    spawn_calls: list[dict[str, object]] = field(default_factory=list)
    _pid: int = 12000

    def spawn_worker(self, **kwargs: object) -> object:
        self.spawn_calls.append(dict(kwargs))
        self._pid += 1
        return ExecutionWorkerHandle(
            target_id=str(kwargs["target_id"]),
            process=_DummyProcess(self._pid),
            stop_event=None,
            control_parent=None,
        )

    def resolve_worker(self, target_id: str) -> object | None:
        _ = target_id
        return None

    def resolve_endpoint(self, target_id: str) -> object | None:
        _ = target_id
        return None

    def stop_worker(self, target_id: str, **kwargs: object) -> bool:
        _ = (target_id, kwargs)
        return False

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {}


class _DummyProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def is_alive(self) -> bool:
        return True


@dataclass(slots=True)
class _LeafWorkerControlPlaneService:
    target: object

    def worker_target(self):
        return self.target


@dataclass(slots=True)
class _RingTopology:
    endpoints_by_worker: dict[str, dict[str, object]]
    calls: list[str] = field(default_factory=list)
    enabled: bool = True

    def pop_child_endpoints(self, *, worker_id: str) -> dict[str, object]:
        self.calls.append(worker_id)
        payload = self.endpoints_by_worker.get(worker_id)
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def plan(self) -> object:
        return SimpleNamespace(enabled=self.enabled)


def test_default_lifecycle_orchestration_service_records_spawn_request_in_state() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=_WorkerLifecycle(),
        endpoint_registry=endpoint_store,
        state=state,
    )
    service.configure_spawn_context(
        child_bundle=None,
        boundary_control_poll_seconds=0.001,
        pipe_codec_mode="pickle",
        worker_target=lambda *_args, **_kwargs: None,
    )
    event = ControlPlaneSpawnRequestedEvent(
        group_name="execution.alpha",
        workers=1,
        nodes=("node.a",),
    )

    service.on_spawn_requested(event)

    events = state.events()
    assert events
    recorded = next(
        item
        for item in events
        if isinstance(item, dict) and item.get("kind") == "control_plane.lifecycle.spawn_requested"
    )
    assert recorded["group_name"] == "execution.alpha"
    assert recorded["workers"] == 1
    assert recorded["nodes"] == ("node.a",)


def test_default_lifecycle_orchestration_service_uses_injected_leaf_worker_target_when_not_explicit() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    target = lambda *_args, **_kwargs: None
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
        leaf_worker_control_plane=_LeafWorkerControlPlaneService(target=target),
    )
    event = ControlPlaneSpawnRequestedEvent(
        group_name="execution.alpha",
        workers=1,
        nodes=("node.a",),
    )

    service.on_spawn_requested(event)

    assert len(lifecycle.spawn_calls) == 1
    assert lifecycle.spawn_calls[0]["target"] is target


def test_default_lifecycle_orchestration_service_resolves_endpoint_from_registry() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    endpoint_store.set("execution.alpha#1", object())
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=_WorkerLifecycle(),
        endpoint_registry=endpoint_store,
        state=state,
    )

    endpoint = service.resolve_registered_endpoint("execution.alpha#1")

    assert endpoint is endpoint_store.get("execution.alpha#1")


def test_default_lifecycle_orchestration_service_spawns_workers_with_bootstrap_contract() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
    )

    def _worker_target(*_args: object, **_kwargs: object) -> None:
        return None

    service.configure_spawn_context(
        child_bundle={"bundle": "raw"},
        boundary_control_poll_seconds=0.125,
        pipe_codec_mode="pickle",
        worker_target=_worker_target,
    )
    event = ControlPlaneSpawnRequestedEvent(
        group_name="execution.alpha",
        workers=2,
        nodes=("node.a", "node.b"),
    )

    service.on_spawn_requested(event)

    assert len(lifecycle.spawn_calls) == 2
    first = lifecycle.spawn_calls[0]
    assert first["target_id"] == "execution.alpha#1"
    assert first["target"] is _worker_target
    assert first["stop_event_position"] == 0
    assert first["child_endpoint_position"] == 1
    assert first["close_child_in_parent"] is True
    assert first["name"] == "sk:execution.alpha#1"
    args = first["args"]
    assert isinstance(args, tuple)
    assert args[0] == {"bundle": "raw"}
    assert args[1] == "execution.alpha#1"
    assert args[2] == "execution.alpha"
    assert args[3] == "async"
    assert args[4] == 0.125
    assert args[5] == "pickle"

    events = state.events()
    assert any(
        isinstance(item, dict) and item.get("kind") == "control_plane.lifecycle.worker_spawned"
        for item in events
    )


def test_default_lifecycle_orchestration_service_respects_explicit_sync_group_profile() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
    )
    service.configure_group_runner_profiles({"execution.alpha": "sync"})
    service.configure_spawn_context(
        child_bundle={"bundle": "raw"},
        boundary_control_poll_seconds=0.125,
        pipe_codec_mode="pickle",
        worker_target=lambda *_args, **_kwargs: None,
    )
    service.on_spawn_requested(
        ControlPlaneSpawnRequestedEvent(
            group_name="execution.alpha",
            workers=1,
            nodes=("node.a",),
        )
    )

    assert len(lifecycle.spawn_calls) == 1
    args = lifecycle.spawn_calls[0]["args"]
    assert isinstance(args, tuple)
    assert args[3] == "sync"


def test_lifecycle_orchestration_service_uses_lifecycle_bundle_projection_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service as service_module

    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
    )

    seen: list[tuple[object | None, str, str | None]] = []

    def _project(
        bundle: object | None,
        group_name: str,
        *,
        runner_profile: str | None = None,
    ) -> object | None:
        seen.append((bundle, group_name, runner_profile))
        return bundle

    monkeypatch.setattr(service_module, "project_child_bundle_for_group", _project)
    service.configure_spawn_context(
        child_bundle={"bundle": "raw"},
        boundary_control_poll_seconds=0.125,
        pipe_codec_mode="pickle",
        worker_target=lambda *_args, **_kwargs: None,
    )

    service.on_spawn_requested(
        ControlPlaneSpawnRequestedEvent(
            group_name="execution.alpha",
            workers=1,
            nodes=("node.a",),
        )
    )

    assert len(lifecycle.spawn_calls) == 1
    assert seen == [({"bundle": "raw"}, "execution.alpha", "async")]


def test_lifecycle_orchestration_service_passes_ring_extra_child_endpoints_to_spawn() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    ring = _RingTopology(
        endpoints_by_worker={
            "execution.alpha#1": {
                "target::ring:execution.alpha#1->execution.beta#1:data": object(),
            }
        }
    )
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
        ring_topology=ring,
    )
    service.configure_spawn_context(
        child_bundle={"bundle": "raw"},
        boundary_control_poll_seconds=0.125,
        pipe_codec_mode="pickle",
        worker_target=lambda *_args, **_kwargs: None,
    )
    service.on_spawn_requested(
        ControlPlaneSpawnRequestedEvent(
            group_name="execution.alpha",
            workers=1,
            nodes=("node.a",),
        )
    )

    assert ring.calls == ["execution.alpha#1"]
    assert len(lifecycle.spawn_calls) == 1
    spawn_call = lifecycle.spawn_calls[0]
    assert isinstance(spawn_call.get("extra_child_endpoints"), dict)
    assert "target::ring:execution.alpha#1->execution.beta#1:data" in spawn_call["extra_child_endpoints"]
    assert spawn_call.get("lane_names") == ("control",)
    spawned_event = next(
        item
        for item in state.events()
        if isinstance(item, dict) and item.get("kind") == "control_plane.lifecycle.worker_spawned"
    )
    assert spawned_event.get("extra_endpoint_count") == 1


def test_lifecycle_orchestration_service_uses_control_and_data_lanes_for_observability_group_in_ring() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    endpoint_store = InMemoryKvStore()
    lifecycle = _WorkerLifecycle()
    ring = _RingTopology(endpoints_by_worker={})
    service = DefaultControlPlaneLifecycleOrchestrationService(
        worker_lifecycle=lifecycle,
        endpoint_registry=endpoint_store,
        state=state,
        ring_topology=ring,
    )
    service.configure_spawn_context(
        child_bundle={
            "runtime": {
                "observability": {
                    "service_process": {
                        "enabled": True,
                        "group_name": "system.observability",
                    }
                }
            }
        },
        boundary_control_poll_seconds=0.125,
        pipe_codec_mode="pickle",
        worker_target=lambda *_args, **_kwargs: None,
    )
    service.on_spawn_requested(
        ControlPlaneSpawnRequestedEvent(
            group_name="system.observability",
            workers=1,
            nodes=("system.obs.log_dispatch",),
        )
    )

    assert len(lifecycle.spawn_calls) == 1
    assert lifecycle.spawn_calls[0].get("lane_names") == ("control", "data")
