from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
    LeafBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    LeafRuntimeActivationService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
    ControlPlaneLeafDiscoverySnapshotApplyService,
)
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafPulse,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.routing.envelope import Envelope


@node(
    name="system.cp.leaf_bootstrap",
    consumes=[ControlPlaneLeafPulse],
    emits=[ControlPlaneLeafHelloEvent],
)
@dataclass
class ControlPlaneLeafBootstrapNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafPulse):
            return []
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        target_group = _leaf_target_group(runtime)
        worker_id = _leaf_worker_id(runtime=runtime, target_group=target_group)
        runner_profile = runtime.get("__runner_profile_requested")
        if not isinstance(runner_profile, str) or not runner_profile:
            runner_profile = None
        return [
            ControlPlaneLeafHelloEvent(
                target_group=target_group,
                worker_id=worker_id,
                pid=os.getpid(),
                runner_profile=runner_profile,
            )
        ]


@dataclass
class ControlPlaneLeafApplyConfigNode:
    bootstrapper: ControlPlaneBootstrapperService = inject.service(ControlPlaneBootstrapperService)
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigCardEvent):
            return []
        try:
            # Leaf discovery is executed in full scope (same universe as root) and
            # contract checks happen only after registry population.
            items = list(self.bootstrapper.discover_all(runtime=_leaf_runtime_from_ctx(_ctx)))
            for item in items:
                self.discovery.append_item(item)
            discovered_nodes = _discovered_node_names(items)
            runtime_nodes = _runtime_node_names_from_ctx(_ctx)
            known_aliases = _expand_known_aliases(set(discovered_nodes) | runtime_nodes)
            missing_nodes = [
                name
                for name in payload.nodes
                if name not in known_aliases and not _is_transport_alias(name)
            ]
            if missing_nodes:
                missing = ", ".join(sorted(set(missing_nodes)))
                return [
                    ControlPlaneLeafConfigAckEvent(
                        target_group=payload.target_group,
                        worker_id=payload.worker_id,
                        config_id=payload.config_id,
                        status="rejected",
                        error=f"missing runtime metadata for nodes: {missing}",
                    )
                ]
            resolved_nodes = tuple(
                name
                for name in payload.nodes
                if name in known_aliases or _is_transport_alias(name)
            )
            return [
                ControlPlaneLeafConfigAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    config_id=payload.config_id,
                    status="applied",
                    resolved_nodes=resolved_nodes,
                )
            ]
        except Exception as exc:
            return [
                ControlPlaneLeafConfigAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    config_id=payload.config_id,
                    status="rejected",
                    error=str(exc) or exc.__class__.__name__,
                )
            ]


@node(
    name="system.cp.leaf_discovery",
    consumes=[ControlPlaneLeafDiscoveryRequestEvent],
    emits=[ControlPlaneLeafDiscoveryAckEvent],
)
@dataclass
class ControlPlaneLeafDiscoveryRequestNode:
    bootstrapper: ControlPlaneBootstrapperService = inject.service(ControlPlaneBootstrapperService)
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafDiscoveryRequestEvent):
            return []
        try:
            items = list(self.bootstrapper.discover_all(runtime=_leaf_runtime_from_ctx(_ctx)))
            for item in items:
                self.discovery.append_item(item)
            discovered_nodes = _discovered_node_names(items)
            missing_nodes = tuple(name for name in payload.required_nodes if name not in discovered_nodes)
            if missing_nodes:
                return [
                    ControlPlaneLeafDiscoveryAckEvent(
                        target_group=payload.target_group,
                        worker_id=payload.worker_id,
                        request_id=payload.request_id,
                        status="rejected",
                        discovered_nodes=discovered_nodes,
                        missing_nodes=missing_nodes,
                        error="missing runtime metadata for nodes: " + ", ".join(sorted(set(missing_nodes))),
                    )
                ]
            resolved = tuple(name for name in payload.required_nodes if name in discovered_nodes)
            return [
                ControlPlaneLeafDiscoveryAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="accepted",
                    discovered_nodes=resolved,
                    missing_nodes=(),
                )
            ]
        except Exception as exc:
            return [
                ControlPlaneLeafDiscoveryAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="rejected",
                    discovered_nodes=(),
                    missing_nodes=tuple(payload.required_nodes),
                    error=str(exc) or exc.__class__.__name__,
                )
            ]


@node(
    name="system.cp.leaf_snapshot_apply",
    consumes=[ControlPlaneLeafDiscoverySnapshotEvent],
    emits=[ControlPlaneLeafDiscoveryAckEvent],
)
@dataclass
class ControlPlaneLeafSnapshotApplyNode:
    snapshot_apply: ControlPlaneLeafDiscoverySnapshotApplyService = inject.service(
        ControlPlaneLeafDiscoverySnapshotApplyService
    )

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafDiscoverySnapshotEvent):
            return []
        session = _leaf_session_from_ctx(ctx, payload=payload)
        return [self.snapshot_apply.apply_snapshot(session=session, snapshot=payload)]


@node(
    name="system.cp.leaf_apply_config",
    consumes=[ControlPlaneLeafConfigCardEvent],
    emits=[ControlPlaneLeafConfigAckEvent],
)
@dataclass
class ControlPlaneLeafConfigApplyRuntimeNode:
    activation: LeafRuntimeActivationService = inject.service(LeafRuntimeActivationService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigCardEvent):
            return []
        session = _leaf_session_from_ctx(ctx, payload=payload)
        return [self.activation.apply_config(session=session, card=payload)]


@node(
    name="system.cp.leaf_boundary_execute",
    consumes=[ControlPlaneLeafBoundaryExecuteCommand],
    emits=[ControlPlaneLeafBoundaryResultEvent],
)
@dataclass
class ControlPlaneLeafBoundaryExecuteNode:
    boundary_execution: LeafBoundaryExecutionService = inject.service(LeafBoundaryExecutionService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafBoundaryExecuteCommand):
            return []
        session = _leaf_session_from_ctx(ctx, payload=payload)
        try:
            outputs = tuple(self.boundary_execution.execute(session=session, inputs=list(payload.inputs)))
            if not payload.finalize:
                return []
            return [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="completed",
                    outputs=outputs,
                )
            ]
        except Exception as exc:  # noqa: BLE001 - typed deterministic error envelope
            if not payload.finalize:
                return []
            return [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="failed",
                    error=str(exc) or exc.__class__.__name__,
                )
            ]


@node(
    name="system.cp.leaf_stop",
    consumes=[ControlPlaneLeafStopCommand],
    emits=[ControlPlaneLeafStopAckEvent],
)
@dataclass
class ControlPlaneLeafStopNode:
    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStopCommand):
            return []
        return [
            ControlPlaneLeafStopAckEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                command_id=payload.command_id,
                status="accepted",
            )
        ]


def _leaf_session_from_ctx(ctx: object | None, *, payload: object) -> object:
    if isinstance(ctx, dict):
        candidate = ctx.get("__leaf_session")
        if candidate is not None:
            return candidate
    group_name = getattr(payload, "target_group", "worker")
    worker_id = getattr(payload, "worker_id", f"{group_name}#1")
    return SimpleNamespace(
        child=None,
        group_name=group_name,
        worker_id=worker_id,
        runner_profile_requested="async",
        runner_profile_effective="async",
    )


def _leaf_target_group(runtime: dict[str, object]) -> str:
    group = runtime.get("__process_group")
    if isinstance(group, str) and group:
        return group
    return "worker"


def _leaf_worker_id(*, runtime: dict[str, object], target_group: str) -> str:
    worker_id = runtime.get("__worker_id")
    if isinstance(worker_id, str) and worker_id:
        return worker_id
    return f"{target_group}#1"


def _leaf_runtime_from_ctx(ctx: object | None) -> dict[str, object]:
    if not isinstance(ctx, dict):
        return {}
    session = ctx.get("__leaf_session")
    child = getattr(session, "child", None)
    runtime = getattr(child, "runtime", None)
    if isinstance(runtime, dict):
        return dict(runtime)
    return {}


def _runtime_node_names_from_ctx(ctx: object | None) -> set[str]:
    if not isinstance(ctx, dict):
        return set()
    session = ctx.get("__leaf_session")
    child = getattr(session, "child", None)
    scenario_steps = getattr(child, "scenario_steps", None)
    if not isinstance(scenario_steps, dict):
        return set()
    return {
        name
        for name in scenario_steps.keys()
        if isinstance(name, str) and name
    }


def _expand_known_aliases(node_names: set[str]) -> set[str]:
    aliases: set[str] = set()
    for name in node_names:
        if not isinstance(name, str) or not name:
            continue
        aliases.add(name)
        if name.endswith("-logical"):
            base = name[: -len("-logical")]
            if base:
                aliases.add(base)
        if name.endswith("_logical"):
            base = name[: -len("_logical")]
            if base:
                aliases.add(base)
    return aliases


def _is_transport_alias(node_name: str) -> bool:
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


def _resolved_nodes_from_discovery(items: list[ControlPlaneDiscoveryItemEvent]) -> tuple[str, ...]:
    return _discovered_node_names(items)


def _discovered_node_names(items: list[object]) -> tuple[str, ...]:
    resolved: list[str] = []
    for item in items:
        name: str | None = None
        if isinstance(item, ControlPlaneDiscoveryItemEvent):
            if item.item_kind != "node":
                continue
            payload_name = item.payload.get("name")
            if isinstance(payload_name, str) and payload_name:
                name = payload_name
        elif isinstance(item, ControlPlaneDiscoveryEntityRecord):
            if item.entity_kind != "node":
                continue
            payload_name = item.meta.get("name")
            if isinstance(payload_name, str) and payload_name:
                name = payload_name
        if not isinstance(name, str) or not name:
            continue
        if isinstance(name, str) and name:
            resolved.append(name)
    return tuple(dict.fromkeys(resolved))


__all__ = [
    "ControlPlaneLeafApplyConfigNode",
    "ControlPlaneLeafDiscoveryRequestNode",
    "ControlPlaneLeafSnapshotApplyNode",
    "ControlPlaneLeafBoundaryExecuteNode",
    "ControlPlaneLeafBootstrapNode",
    "ControlPlaneLeafConfigApplyRuntimeNode",
    "ControlPlaneLeafStopNode",
]
