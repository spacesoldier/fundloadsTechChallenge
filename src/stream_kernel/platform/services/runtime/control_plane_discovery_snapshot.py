from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
)


@runtime_checkable
class ControlPlaneDiscoverySnapshotVerificationAdapter(Protocol):
    def verify_snapshot(
        self,
        *,
        runtime: dict[str, object],
        required_nodes: tuple[str, ...],
        snapshot_records: tuple[ControlPlaneDiscoveryEntityRecord, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
        raise NotImplementedError


@adapter(
    name="control_plane_discovery_snapshot_verifier",
    kind="control_plane.discovery.snapshot_verifier",
    consumes=[ControlPlaneLeafDiscoverySnapshotEvent],
    emits=[ControlPlaneLeafDiscoveryAckEvent],
)
def control_plane_discovery_snapshot_verifier_adapter(
    settings: dict[str, object],
) -> ControlPlaneDiscoverySnapshotVerificationAdapter:
    _ = settings
    return DefaultControlPlaneDiscoverySnapshotVerificationAdapter()


@service(name="control_plane_discovery_snapshot_verification_adapter")
@dataclass(slots=True)
class DefaultControlPlaneDiscoverySnapshotVerificationAdapter(
    ControlPlaneDiscoverySnapshotVerificationAdapter
):
    def verify_snapshot(
        self,
        *,
        runtime: dict[str, object],
        required_nodes: tuple[str, ...],
        snapshot_records: tuple[ControlPlaneDiscoveryEntityRecord, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
        _ = runtime
        by_name: dict[str, ControlPlaneDiscoveryEntityRecord] = {}
        for record in snapshot_records:
            if record.entity_kind != "node":
                continue
            name = record.meta.get("name")
            if isinstance(name, str) and name:
                by_name[name] = record

        discovered: list[str] = []
        missing: list[str] = []
        errors: list[str] = []
        for node_name in required_nodes:
            if _is_transport_alias(node_name):
                discovered.append(node_name)
                continue
            record = by_name.get(node_name)
            if record is None:
                missing.append(node_name)
                continue
            if not self._is_record_resolvable(record):
                missing.append(node_name)
                errors.append(node_name)
                continue
            discovered.append(node_name)

        error = None
        if errors:
            error = "snapshot node import resolution failed: " + ", ".join(sorted(dict.fromkeys(errors)))
        elif missing:
            error = "missing runtime metadata for nodes: " + ", ".join(sorted(dict.fromkeys(missing)))
        return (tuple(dict.fromkeys(discovered)), tuple(dict.fromkeys(missing)), error)

    @staticmethod
    def _is_record_resolvable(record: ControlPlaneDiscoveryEntityRecord) -> bool:
        try:
            module = importlib.import_module(record.module)
        except Exception:
            return False
        current = module
        for part in record.qualname.split("."):
            if not part or part == "<locals>":
                continue
            current = getattr(current, part, None)
            if current is None:
                return False
        return True


@runtime_checkable
class ControlPlaneLeafDiscoverySnapshotApplyService(Protocol):
    def apply_snapshot(
        self,
        *,
        session: object,
        snapshot: ControlPlaneLeafDiscoverySnapshotEvent,
    ) -> ControlPlaneLeafDiscoveryAckEvent:
        raise NotImplementedError


@service(name="control_plane_leaf_discovery_snapshot_apply_service")
@dataclass(slots=True)
class DefaultControlPlaneLeafDiscoverySnapshotApplyService(
    ControlPlaneLeafDiscoverySnapshotApplyService
):
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)
    verifier: ControlPlaneDiscoverySnapshotVerificationAdapter = inject.service(
        ControlPlaneDiscoverySnapshotVerificationAdapter
    )

    def apply_snapshot(
        self,
        *,
        session: object,
        snapshot: ControlPlaneLeafDiscoverySnapshotEvent,
    ) -> ControlPlaneLeafDiscoveryAckEvent:
        appender = getattr(self.discovery, "append_item", None)
        if callable(appender):
            for record in snapshot.snapshot_records:
                appender(record)

        verifier = self.verifier
        if not isinstance(verifier, ControlPlaneDiscoverySnapshotVerificationAdapter):
            verifier = DefaultControlPlaneDiscoverySnapshotVerificationAdapter()
        runtime = self._runtime_from_session(session)
        discovered_nodes, missing_nodes, error = verifier.verify_snapshot(
            runtime=runtime,
            required_nodes=snapshot.required_nodes,
            snapshot_records=snapshot.snapshot_records,
        )
        if missing_nodes or error is not None:
            return ControlPlaneLeafDiscoveryAckEvent(
                target_group=snapshot.target_group,
                worker_id=snapshot.worker_id,
                request_id=snapshot.request_id,
                status="rejected",
                discovered_nodes=discovered_nodes,
                missing_nodes=missing_nodes,
                error=error
                or "missing runtime metadata for nodes: " + ", ".join(sorted(set(missing_nodes))),
            )
        return ControlPlaneLeafDiscoveryAckEvent(
            target_group=snapshot.target_group,
            worker_id=snapshot.worker_id,
            request_id=snapshot.request_id,
            status="accepted",
            discovered_nodes=discovered_nodes or tuple(snapshot.required_nodes),
            missing_nodes=(),
        )

    @staticmethod
    def _runtime_from_session(session: object) -> dict[str, object]:
        child = getattr(session, "child", None)
        runtime = getattr(child, "runtime", None)
        if isinstance(runtime, dict):
            return dict(runtime)
        return {}


__all__ = [
    "ControlPlaneDiscoverySnapshotVerificationAdapter",
    "DefaultControlPlaneDiscoverySnapshotVerificationAdapter",
    "control_plane_discovery_snapshot_verifier_adapter",
    "ControlPlaneLeafDiscoverySnapshotApplyService",
    "DefaultControlPlaneLeafDiscoverySnapshotApplyService",
]


def _is_transport_alias(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    if node_name.startswith("system.obs."):
        return True
    if node_name.startswith(("source:", "sink:")):
        return True
    if node_name.endswith("_bridge"):
        return True
    if "_line_bridge" in node_name:
        return True
    return False
