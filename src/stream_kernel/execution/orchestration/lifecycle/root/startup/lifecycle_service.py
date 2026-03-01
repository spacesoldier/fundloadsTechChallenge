from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.child_bundle import (
    project_child_bundle_for_group,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service import (
    LeafWorkerControlPlaneService,
)
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcEndpointRegistry
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.lifecycle import ExecutionWorkerLifecycleService


@runtime_checkable
class ControlPlaneLifecycleOrchestrationService(Protocol):
    # Orchestration bridge between control-plane spawn intents and platform lifecycle rails.
    # This slice records intents + exposes endpoint lookup via existing platform ports.
    def on_spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> None:
        raise NotImplementedError(
            "ControlPlaneLifecycleOrchestrationService.on_spawn_requested must be implemented"
        )

    def resolve_registered_endpoint(self, target_id: str) -> object | None:
        raise NotImplementedError(
            "ControlPlaneLifecycleOrchestrationService.resolve_registered_endpoint must be implemented"
        )

    def configure_spawn_context(
        self,
        *,
        child_bundle: object | None,
        boundary_control_poll_seconds: float,
        pipe_codec_mode: str,
        worker_target: Callable[..., object] | None = None,
    ) -> None:
        raise NotImplementedError(
            "ControlPlaneLifecycleOrchestrationService.configure_spawn_context must be implemented"
        )


@service(name="control_plane_lifecycle_orchestration_service")
@dataclass(slots=True)
class DefaultControlPlaneLifecycleOrchestrationService(ControlPlaneLifecycleOrchestrationService):
    # NOTE: We intentionally inject existing platform rails instead of inventing a new port kind.
    worker_lifecycle: object = inject.service(ExecutionWorkerLifecycleService)
    endpoint_registry: object = inject.kv(ExecutionIpcEndpointRegistry)
    state: object = inject.service(ControlPlaneStateService)
    leaf_worker_control_plane: object = inject.service(LeafWorkerControlPlaneService)
    _child_bundle: object | None = None
    _boundary_control_poll_seconds: float = 0.001
    _pipe_codec_mode: str = "pickle"
    _worker_target: Callable[..., object] | None = None
    _group_runner_profiles: dict[str, str] = field(default_factory=dict)

    def on_spawn_requested(self, event: ControlPlaneSpawnRequestedEvent) -> None:
        if not isinstance(event, ControlPlaneSpawnRequestedEvent):
            raise ValueError("on_spawn_requested requires ControlPlaneSpawnRequestedEvent")
        state = self._state()
        state.append_event(
            {
                "kind": "control_plane.lifecycle.spawn_requested",
                "group_name": event.group_name,
                "workers": event.workers,
                "nodes": tuple(event.nodes),
                "worker_lifecycle_snapshot_size": len(self._worker_lifecycle().snapshot()),
            }
        )
        lifecycle = self._worker_lifecycle()
        worker_target = self._resolve_worker_target()
        runner_profile = self._group_runner_profiles.get(event.group_name, "async")
        for index in range(event.workers):
            worker_id = f"{event.group_name}#{index + 1}"
            worker_bundle = project_child_bundle_for_group(
                self._child_bundle,
                event.group_name,
                runner_profile=runner_profile,
            )
            handle = lifecycle.spawn_worker(
                target_id=worker_id,
                target=worker_target,
                args=(
                    worker_bundle,
                    worker_id,
                    event.group_name,
                    runner_profile,
                    self._boundary_control_poll_seconds,
                    self._pipe_codec_mode,
                ),
                name=f"sk:{worker_id}",
                daemon=True,
                start=True,
                stop_event_position=0,
                child_endpoint_position=1,
                close_child_in_parent=True,
            )
            state.append_event(
                {
                    "kind": "control_plane.lifecycle.worker_spawned",
                    "group_name": event.group_name,
                    "worker_id": worker_id,
                    "pid": getattr(getattr(handle, "process", None), "pid", None),
                    "runner_profile": runner_profile,
                    "nodes": tuple(event.nodes),
                }
            )

    def resolve_registered_endpoint(self, target_id: str) -> object | None:
        if not isinstance(target_id, str) or not target_id:
            return None
        registry = self._endpoint_registry()
        return registry.get(target_id)

    def configure_spawn_context(
        self,
        *,
        child_bundle: object | None,
        boundary_control_poll_seconds: float,
        pipe_codec_mode: str,
        worker_target: Callable[..., object] | None = None,
    ) -> None:
        self._child_bundle = child_bundle
        if isinstance(boundary_control_poll_seconds, (int, float)) and boundary_control_poll_seconds > 0:
            self._boundary_control_poll_seconds = float(boundary_control_poll_seconds)
        if isinstance(pipe_codec_mode, str) and pipe_codec_mode:
            self._pipe_codec_mode = pipe_codec_mode
        if worker_target is not None:
            self._worker_target = worker_target

    def configure_group_runner_profiles(self, profiles: dict[str, str] | None) -> None:
        if not isinstance(profiles, dict):
            self._group_runner_profiles = {}
            return
        self._group_runner_profiles = {
            str(name): profile
            for name, profile in profiles.items()
            if isinstance(name, str) and name and isinstance(profile, str) and profile
        }

    def _worker_lifecycle(self) -> ExecutionWorkerLifecycleService:
        candidate = self.worker_lifecycle
        if isinstance(candidate, ExecutionWorkerLifecycleService):
            return candidate
        if callable(getattr(candidate, "snapshot", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionWorkerLifecycleService binding is required")

    def _endpoint_registry(self) -> KVStore:
        candidate = self.endpoint_registry
        if isinstance(candidate, KVStore):
            return candidate
        if callable(getattr(candidate, "get", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ExecutionIpcEndpointRegistry KV binding is required")

    def _state(self) -> ControlPlaneStateService:
        candidate = self.state
        if isinstance(candidate, ControlPlaneStateService):
            return candidate
        if callable(getattr(candidate, "append_event", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneStateService binding is required")

    def _resolve_worker_target(self) -> Callable[..., object]:
        if self._worker_target is not None:
            return self._worker_target
        leaf_worker_cp = self.leaf_worker_control_plane
        if isinstance(leaf_worker_cp, LeafWorkerControlPlaneService):
            target = leaf_worker_cp.worker_target()
            if callable(target):
                return target
        getter = getattr(leaf_worker_cp, "worker_target", None)
        if callable(getter):
            target = getter()
            if callable(target):
                return target
        raise ValueError("worker_target must be configured or provided by LeafWorkerControlPlaneService")


__all__ = [
    "ControlPlaneLifecycleOrchestrationService",
    "DefaultControlPlaneLifecycleOrchestrationService",
]
