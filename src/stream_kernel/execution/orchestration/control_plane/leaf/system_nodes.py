from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import leaf_debug_log
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
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
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafPulse,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    ControlPlaneLeafShutdownReadinessService,
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
        leaf_debug_log(
            event="leaf.node.bootstrap.received",
            node_name="system.cp.leaf_bootstrap",
            payload_type=type(payload).__name__,
        )
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        target_group = _leaf_target_group(runtime)
        worker_id = _leaf_worker_id(runtime=runtime, target_group=target_group)
        runner_profile = runtime.get("__runner_profile_requested")
        if not isinstance(runner_profile, str) or not runner_profile:
            runner_profile = None
        produced = [
            ControlPlaneLeafHelloEvent(
                target_group=target_group,
                worker_id=worker_id,
                pid=os.getpid(),
                runner_profile=runner_profile,
            )
        ]
        leaf_debug_log(
            event="leaf.node.bootstrap.produced",
            node_name="system.cp.leaf_bootstrap",
            produced_count=len(produced),
            worker_id=worker_id,
            target_group=target_group,
        )
        return produced


@dataclass
class ControlPlaneLeafApplyConfigNode:
    bootstrapper: ControlPlaneBootstrapperService = inject.service(ControlPlaneBootstrapperService)
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigCardEvent):
            return []
        leaf_debug_log(
            event="leaf.node.apply_config.received",
            node_name="system.cp.leaf_apply_config",
            config_id=payload.config_id,
            node_count=len(payload.nodes),
        )
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
                produced = [
                    ControlPlaneLeafConfigAckEvent(
                        target_group=payload.target_group,
                        worker_id=payload.worker_id,
                        config_id=payload.config_id,
                        status="rejected",
                        error=f"missing runtime metadata for nodes: {missing}",
                    )
                ]
                leaf_debug_log(
                    event="leaf.node.apply_config.rejected",
                    node_name="system.cp.leaf_apply_config",
                    config_id=payload.config_id,
                    missing_nodes=sorted(set(missing_nodes)),
                )
                return produced
            resolved_nodes = tuple(
                name
                for name in payload.nodes
                if name in known_aliases or _is_transport_alias(name)
            )
            produced = [
                ControlPlaneLeafConfigAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    config_id=payload.config_id,
                    status="applied",
                    resolved_nodes=resolved_nodes,
                )
            ]
            leaf_debug_log(
                event="leaf.node.apply_config.applied",
                node_name="system.cp.leaf_apply_config",
                config_id=payload.config_id,
                resolved_count=len(resolved_nodes),
            )
            return produced
        except Exception as exc:
            leaf_debug_log(
                event="leaf.node.apply_config.error",
                node_name="system.cp.leaf_apply_config",
                config_id=payload.config_id,
                error=exc.__class__.__name__,
            )
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
        leaf_debug_log(
            event="leaf.node.discovery.received",
            node_name="system.cp.leaf_discovery",
            request_id=payload.request_id,
            required_count=len(payload.required_nodes),
        )
        try:
            items = list(self.bootstrapper.discover_all(runtime=_leaf_runtime_from_ctx(_ctx)))
            for item in items:
                self.discovery.append_item(item)
            discovered_nodes = _discovered_node_names(items)
            missing_nodes = tuple(name for name in payload.required_nodes if name not in discovered_nodes)
            if missing_nodes:
                produced = [
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
                leaf_debug_log(
                    event="leaf.node.discovery.rejected",
                    node_name="system.cp.leaf_discovery",
                    request_id=payload.request_id,
                    missing_count=len(missing_nodes),
                )
                return produced
            resolved = tuple(name for name in payload.required_nodes if name in discovered_nodes)
            produced = [
                ControlPlaneLeafDiscoveryAckEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="accepted",
                    discovered_nodes=resolved,
                    missing_nodes=(),
                )
            ]
            leaf_debug_log(
                event="leaf.node.discovery.accepted",
                node_name="system.cp.leaf_discovery",
                request_id=payload.request_id,
                resolved_count=len(resolved),
            )
            return produced
        except Exception as exc:
            leaf_debug_log(
                event="leaf.node.discovery.error",
                node_name="system.cp.leaf_discovery",
                request_id=payload.request_id,
                error=exc.__class__.__name__,
            )
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
        leaf_debug_log(
            event="leaf.node.snapshot_apply.received",
            node_name="system.cp.leaf_snapshot_apply",
            request_id=payload.request_id,
            record_count=len(payload.snapshot_records),
        )
        session = _leaf_session_from_ctx(ctx, payload=payload)
        result = self.snapshot_apply.apply_snapshot(session=session, snapshot=payload)
        leaf_debug_log(
            event="leaf.node.snapshot_apply.processed",
            node_name="system.cp.leaf_snapshot_apply",
            request_id=payload.request_id,
            status=getattr(result, "status", None),
        )
        return [result]


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
        leaf_debug_log(
            event="leaf.node.apply_runtime_config.received",
            node_name="system.cp.leaf_apply_config",
            config_id=payload.config_id,
            node_count=len(payload.nodes),
        )
        session = _leaf_session_from_ctx(ctx, payload=payload)
        result = self.activation.apply_config(session=session, card=payload)
        leaf_debug_log(
            event="leaf.node.apply_runtime_config.processed",
            node_name="system.cp.leaf_apply_config",
            config_id=payload.config_id,
            status=result.status,
            error=result.error,
        )
        return [result]


@node(
    name="system.cp.leaf_start_work",
    consumes=[ControlPlaneLeafStartWorkEvent],
    emits=[Envelope],
)
@dataclass
class ControlPlaneLeafStartWorkNode:
    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStartWorkEvent):
            return []
        leaf_debug_log(
            event="leaf.node.start_work.received",
            node_name="system.cp.leaf_start_work",
            source_targets=list(payload.source_targets),
        )
        runtime_nodes = _runtime_node_names_from_ctx(ctx)
        local_sources = [name for name in runtime_nodes if isinstance(name, str) and name.startswith("source:")]
        if not local_sources:
            leaf_debug_log(
                event="leaf.node.start_work.produced",
                node_name="system.cp.leaf_start_work",
                produced_count=0,
                targets=[],
            )
            return []
        requested = list(payload.source_targets) if payload.source_targets else list(local_sources)
        allowed = set(local_sources)
        unique_targets: list[str] = []
        seen: set[str] = set()
        for target in requested:
            if not isinstance(target, str) or not target:
                continue
            if target not in allowed:
                continue
            if target in seen:
                continue
            seen.add(target)
            unique_targets.append(target)
        produced = [
            Envelope(
                payload=BootstrapControl(target=target),
                target=target,
            )
            for target in unique_targets
        ]
        leaf_debug_log(
            event="leaf.node.start_work.produced",
            node_name="system.cp.leaf_start_work",
            produced_count=len(produced),
            targets=[item.target for item in produced],
        )
        return produced


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
        leaf_debug_log(
            event="leaf.node.boundary_execute.received",
            node_name="system.cp.leaf_boundary_execute",
            request_id=payload.request_id,
            input_count=len(payload.inputs),
            finalize=payload.finalize,
        )
        session = _leaf_session_from_ctx(ctx, payload=payload)
        try:
            outputs = tuple(self.boundary_execution.execute(session=session, inputs=list(payload.inputs)))
            if not payload.finalize:
                leaf_debug_log(
                    event="leaf.node.boundary_execute.background_completed",
                    node_name="system.cp.leaf_boundary_execute",
                    request_id=payload.request_id,
                    output_count=len(outputs),
                )
                return []
            produced = [
                ControlPlaneLeafBoundaryResultEvent(
                    target_group=payload.target_group,
                    worker_id=payload.worker_id,
                    request_id=payload.request_id,
                    status="completed",
                    outputs=outputs,
                )
            ]
            leaf_debug_log(
                event="leaf.node.boundary_execute.completed",
                node_name="system.cp.leaf_boundary_execute",
                request_id=payload.request_id,
                output_count=len(outputs),
                sample_targets=[
                    item.target
                    for item in outputs
                    if isinstance(item, Envelope)
                    and isinstance(item.target, str)
                    and item.target
                ][:16],
            )
            return produced
        except Exception as exc:  # noqa: BLE001 - typed deterministic error envelope
            if not payload.finalize:
                leaf_debug_log(
                    event="leaf.node.boundary_execute.background_failed",
                    node_name="system.cp.leaf_boundary_execute",
                    request_id=payload.request_id,
                    error=exc.__class__.__name__,
                )
                return []
            leaf_debug_log(
                event="leaf.node.boundary_execute.failed",
                node_name="system.cp.leaf_boundary_execute",
                request_id=payload.request_id,
                error=exc.__class__.__name__,
            )
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
    name="system.cp.leaf_tombstone_finalize",
    consumes=[ControlPlaneLeafBoundaryResultEvent],
    emits=[ControlPlaneLeafDrainReadyEvent],
)
@dataclass
class ControlPlaneLeafTombstoneFinalizeNode:
    readiness: ControlPlaneLeafShutdownReadinessService = inject.service(
        ControlPlaneLeafShutdownReadinessService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafBoundaryResultEvent):
            return []
        event = self.readiness.observe_boundary_result(payload)
        if event is None:
            return []
        return [event]


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
        leaf_debug_log(
            event="leaf.node.stop.received",
            node_name="system.cp.leaf_stop",
            command_id=payload.command_id,
        )
        produced = [
            ControlPlaneLeafStopAckEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                command_id=payload.command_id,
                status="accepted",
            )
        ]
        leaf_debug_log(
            event="leaf.node.stop.ack_produced",
            node_name="system.cp.leaf_stop",
            command_id=payload.command_id,
        )
        return produced


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
    if node_name.startswith("system.transport.handoff."):
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
    "ControlPlaneLeafStartWorkNode",
    "ControlPlaneLeafBoundaryExecuteNode",
    "ControlPlaneLeafTombstoneFinalizeNode",
    "ControlPlaneLeafBootstrapNode",
    "ControlPlaneLeafConfigApplyRuntimeNode",
    "ControlPlaneLeafStopNode",
]
