from __future__ import annotations

import os
from dataclasses import dataclass

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafHelloEvent,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(frozen=True, slots=True)
class LeafWorkerRuntimeSession:
    child: object
    worker_id: str
    group_name: str
    runner_profile_requested: str
    runner_profile_effective: str | None = None


def bootstrap_leaf_worker_runtime_from_bundle(
    *,
    bundle: object,
    worker_id: str,
    group_name: str,
    runner_profile_requested: str,
) -> LeafWorkerRuntimeSession:
    child = resolve_leaf_runtime_bootstrap_service().bootstrap_runtime(bundle=bundle)
    runner_profile_effective = getattr(child, "runner_profile_effective", None)
    if not isinstance(runner_profile_effective, str) or not runner_profile_effective:
        runner_profile_effective = None
    return LeafWorkerRuntimeSession(
        child=child,
        worker_id=worker_id,
        group_name=group_name,
        runner_profile_requested=runner_profile_requested,
        runner_profile_effective=runner_profile_effective,
    )


def build_leaf_hello_event(session: LeafWorkerRuntimeSession) -> ControlPlaneLeafHelloEvent:
    return ControlPlaneLeafHelloEvent(
        target_group=session.group_name,
        worker_id=session.worker_id,
        pid=os.getpid(),
        runner_profile=session.runner_profile_effective or session.runner_profile_requested,
    )


def execute_leaf_boundary_batch(
    *,
    session: LeafWorkerRuntimeSession,
    inputs: list[object],
    finalize_runtime: bool = False,
) -> list[Envelope]:
    return resolve_leaf_runtime_boundary_service().execute_boundary_batch(
        child=session.child,
        inputs=list(inputs),
        finalize_runtime=bool(finalize_runtime),
    )


def resolve_leaf_runtime_bootstrap_service():
    from ..startup.runtime_bootstrap_service import DefaultLeafRuntimeBootstrapService

    return DefaultLeafRuntimeBootstrapService()


def resolve_leaf_runtime_boundary_service():
    from .runtime_boundary_service import DefaultLeafRuntimeBoundaryBatchService

    return DefaultLeafRuntimeBoundaryBatchService()


__all__ = [
    "LeafWorkerRuntimeSession",
    "bootstrap_leaf_worker_runtime_from_bundle",
    "build_leaf_hello_event",
    "execute_leaf_boundary_batch",
    "resolve_leaf_runtime_bootstrap_service",
    "resolve_leaf_runtime_boundary_service",
]
