from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node_annotation import node
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import (
    LeafLifecycleDebugLoggingService,
    leaf_debug_log,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.command.channel_services import (
    LeafCommandChannelIngressService,
    LeafControlReplyDispatchService,
    LeafRunnerControlService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.session_state_service import (
    LeafRuntimeSessionStateService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
    LeafBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    LeafRuntimeActivationService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
    ExecutionIpcTransportService,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
    ControlPlaneLeafDiscoverySnapshotApplyService,
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
    ControlPlaneReadyForWorkEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafPulse,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    ControlPlaneLeafShutdownReadinessService,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerService,
    PlatformSchedulerTimerService,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.routing.envelope import Envelope

LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME = "source:system.cp.command_ingress"
LEAF_COMMAND_INGRESS_SOURCE_NODE_PREFIX = f"{LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME}:"


def _emit_leaf_debug(
    debug_logging: object | None,
    *,
    event: str,
    **fields: object,
) -> None:
    service = debug_logging if isinstance(debug_logging, LeafLifecycleDebugLoggingService) else None
    leaf_debug_log(event=event, service=service, **fields)


def leaf_command_ingress_source_node_name(*, lane: str) -> str:
    lane_name = lane if isinstance(lane, str) and lane else EXECUTION_IPC_LANE_CONTROL
    return f"{LEAF_COMMAND_INGRESS_SOURCE_NODE_PREFIX}{lane_name}"


def is_leaf_command_ingress_source_node_name(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    return node_name == LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME or node_name.startswith(
        LEAF_COMMAND_INGRESS_SOURCE_NODE_PREFIX
    )


def leaf_command_ingress_source_lanes() -> tuple[str, ...]:
    return (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
    )


@node(
    name="system.cp.bootstrap_dispatch",
    consumes=[ControlPlaneReadyForWorkEvent],
    emits=[ControlPlaneLeafPulse],
)
@dataclass
class ControlPlaneLeafBootstrapDispatchNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneReadyForWorkEvent):
            return []
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        process_role = runtime.get("__process_role")
        if not isinstance(process_role, str) or process_role not in {"worker", "observability_worker"}:
            return []
        return [ControlPlaneLeafPulse(runtime=runtime)]


@node(
    name="system.cp.leaf_bootstrap",
    consumes=[ControlPlaneLeafPulse],
    emits=[ControlPlaneLeafHelloEvent],
)
@dataclass
class ControlPlaneLeafBootstrapNode:
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafPulse):
            return []
        _emit_leaf_debug(
            self.debug_logging,
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
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.bootstrap.produced",
            node_name="system.cp.leaf_bootstrap",
            produced_count=len(produced),
            worker_id=worker_id,
            target_group=target_group,
        )
        return produced


@node(
    name=LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME,
    consumes=[BootstrapControl],
    emits=[
        ControlPlaneLeafDiscoveryRequestEvent,
        ControlPlaneLeafDiscoverySnapshotEvent,
        ControlPlaneLeafConfigCardEvent,
        ControlPlaneLeafStartWorkEvent,
        ControlPlaneLeafBoundaryExecuteCommand,
        ControlPlaneLeafStopCommand,
    ],
)
@dataclass
class ControlPlaneLeafCommandIngressSourceNode:
    ingress: LeafCommandChannelIngressService = inject.service(LeafCommandChannelIngressService)
    runner_control: LeafRunnerControlService = inject.service(LeafRunnerControlService)
    scheduler: PlatformSchedulerService = inject.service(PlatformSchedulerService)
    timer: PlatformSchedulerTimerService | None = inject.service(PlatformSchedulerTimerService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)
    lane: str = EXECUTION_IPC_LANE_CONTROL
    source_name: str = LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME
    poll_interval_seconds: float = 0.01
    _scheduler_registered: bool = field(default=False, init=False, repr=False)

    def initialize(self) -> None:
        if self._scheduler_registered:
            return
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME
        )
        interval = max(0.001, float(self.poll_interval_seconds))
        self.scheduler.apply_command(
            PlatformSchedulerUpsertCommand(
                job_id=f"cp.leaf.command_ingress:{source_name}",
                target=source_name,
                interval_seconds=interval,
                run_immediately=True,
                payload=BootstrapControl(target=source_name),
            )
        )
        apply_timer_command = getattr(self.timer, "apply_command", None)
        if callable(apply_timer_command):
            apply_timer_command(
                PlatformSchedulerUpsertCommand(
                    job_id=f"cp.leaf.command_ingress:{source_name}",
                    target=source_name,
                    interval_seconds=interval,
                    run_immediately=True,
                    payload=BootstrapControl(target=source_name),
                )
            )
        self._scheduler_registered = True

    async def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, BootstrapControl):
            return []
        if payload.target != self.source_name:
            return []
        if self.runner_control.stop_requested():
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.command_source.stop_requested",
                node_name=self.source_name,
                lane=self.lane,
            )
            return []
        worker_id = _leaf_worker_id_from_env()
        poll_timeout_seconds = 0.0
        poll_for_lane_async = getattr(self.ingress, "poll_next_message_for_lane_async", None)
        poll_for_lane = getattr(self.ingress, "poll_next_message_for_lane", None)
        if callable(poll_for_lane_async):
            next_message = await poll_for_lane_async(
                worker_id=worker_id,
                lane=self.lane,
                timeout_seconds=poll_timeout_seconds,
            )
        elif callable(poll_for_lane):
            next_message = poll_for_lane(
                worker_id=worker_id,
                lane=self.lane,
                timeout_seconds=poll_timeout_seconds,
            )
        else:
            next_message = self.ingress.poll_next_message(worker_id=worker_id)
        if next_message is None:
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.command_source.message_polled",
            node_name=self.source_name,
            lane=self.lane,
            payload_type=type(next_message).__name__,
        )
        return [next_message]


@node(
    name="system.cp.leaf_reply_dispatch",
    consumes=[
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafDiscoveryAckEvent,
        ControlPlaneLeafConfigAckEvent,
        ControlPlaneLeafBoundaryResultEvent,
        ControlPlaneLeafDrainReadyEvent,
        ControlPlaneLeafStopAckEvent,
    ],
    emits=[],
)
@dataclass
class ControlPlaneLeafReplyDispatchNode:
    reply_dispatch: LeafControlReplyDispatchService = inject.service(LeafControlReplyDispatchService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(
            payload,
            (
                ControlPlaneLeafHelloEvent,
                ControlPlaneLeafDiscoveryAckEvent,
                ControlPlaneLeafConfigAckEvent,
                ControlPlaneLeafBoundaryResultEvent,
                ControlPlaneLeafDrainReadyEvent,
                ControlPlaneLeafStopAckEvent,
            ),
        ):
            return []
        worker_id = payload.worker_id
        if not isinstance(worker_id, str) or not worker_id:
            worker_id = _leaf_worker_id_from_env()
        self.reply_dispatch.dispatch_reply(worker_id=worker_id, payload=payload)
        return []


@dataclass
class ControlPlaneLeafApplyConfigNode:
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigCardEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.apply_config.received",
            node_name="system.cp.leaf_apply_config",
            config_id=payload.config_id,
            node_count=len(payload.nodes),
        )
        try:
            discovered_nodes = _discovered_node_names(self.discovery.items())
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
                _emit_leaf_debug(
                    self.debug_logging,
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
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.apply_config.applied",
                node_name="system.cp.leaf_apply_config",
                config_id=payload.config_id,
                resolved_count=len(resolved_nodes),
            )
            return produced
        except Exception as exc:
            _emit_leaf_debug(
                self.debug_logging,
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
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafDiscoveryRequestEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.discovery.received",
            node_name="system.cp.leaf_discovery",
            request_id=payload.request_id,
            required_count=len(payload.required_nodes),
        )
        try:
            discovered_nodes = _discovered_node_names(self.discovery.items())
            runtime_nodes = _runtime_node_names_from_ctx(_ctx)
            known_aliases = _expand_known_aliases(set(discovered_nodes) | runtime_nodes)
            missing_nodes = tuple(
                name
                for name in payload.required_nodes
                if name not in known_aliases and not _is_transport_alias(name)
            )
            resolved = tuple(
                name
                for name in payload.required_nodes
                if name in known_aliases or _is_transport_alias(name)
            )
            if missing_nodes:
                produced = [
                    ControlPlaneLeafDiscoveryAckEvent(
                        target_group=payload.target_group,
                        worker_id=payload.worker_id,
                        request_id=payload.request_id,
                        status="rejected",
                        discovered_nodes=resolved,
                        missing_nodes=missing_nodes,
                        error="missing runtime metadata for nodes: " + ", ".join(sorted(set(missing_nodes))),
                    )
                ]
                _emit_leaf_debug(
                    self.debug_logging,
                    event="leaf.node.discovery.rejected",
                    node_name="system.cp.leaf_discovery",
                    request_id=payload.request_id,
                    missing_count=len(missing_nodes),
                )
                return produced
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
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.discovery.accepted",
                node_name="system.cp.leaf_discovery",
                request_id=payload.request_id,
                resolved_count=len(resolved),
            )
            return produced
        except Exception as exc:
            _emit_leaf_debug(
                self.debug_logging,
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
    session_state: LeafRuntimeSessionStateService = inject.service(LeafRuntimeSessionStateService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafDiscoverySnapshotEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.snapshot_apply.received",
            node_name="system.cp.leaf_snapshot_apply",
            request_id=payload.request_id,
            record_count=len(payload.snapshot_records),
        )
        session = _leaf_session_from_ctx(ctx, payload=payload, session_state=self.session_state)
        result = self.snapshot_apply.apply_snapshot(session=session, snapshot=payload)
        _emit_leaf_debug(
            self.debug_logging,
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
    session_state: LeafRuntimeSessionStateService = inject.service(LeafRuntimeSessionStateService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigCardEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.apply_runtime_config.received",
            node_name="system.cp.leaf_apply_config",
            config_id=payload.config_id,
            node_count=len(payload.nodes),
        )
        session = _leaf_session_from_ctx(ctx, payload=payload, session_state=self.session_state)
        result = self.activation.apply_config(session=session, card=payload)
        _emit_leaf_debug(
            self.debug_logging,
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
    emits=[ControlPlaneLeafBoundaryExecuteCommand],
)
@dataclass
class ControlPlaneLeafStartWorkNode:
    session_state: LeafRuntimeSessionStateService = inject.service(LeafRuntimeSessionStateService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStartWorkEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.start_work.received",
            node_name="system.cp.leaf_start_work",
            source_targets=list(payload.source_targets),
        )
        session = _leaf_session_from_ctx(ctx, payload=payload, session_state=self.session_state)
        runtime_nodes = _runtime_node_names_from_ctx(ctx, session_state=self.session_state)
        local_sources = [name for name in runtime_nodes if isinstance(name, str) and name.startswith("source:")]
        if not local_sources:
            _emit_leaf_debug(
                self.debug_logging,
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
        if not unique_targets:
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.start_work.produced",
                node_name="system.cp.leaf_start_work",
                produced_count=0,
                targets=[],
            )
            return []
        session_group = getattr(session, "group_name", None)
        if not isinstance(session_group, str) or not session_group:
            session_group = os.environ.get("STREAM_KERNEL_PROCESS_GROUP") or "worker"
        session_worker_id = getattr(session, "worker_id", None)
        if not isinstance(session_worker_id, str) or not session_worker_id:
            session_worker_id = _leaf_worker_id_from_env()
        produced = [
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group=session_group,
                worker_id=session_worker_id,
                request_id=f"start-work:{session_worker_id}:{time.time_ns()}:{index + 1}",
                inputs=(
                    {
                        "dispatch_group": session_group,
                        "target": target,
                        "payload": BootstrapControl(target=target),
                        "trace_id": None,
                        "reply_to": None,
                        "span_id": None,
                        "tombstone": False,
                    },
                ),
                finalize=True,
            )
            for index, target in enumerate(unique_targets)
        ]
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.start_work.produced",
            node_name="system.cp.leaf_start_work",
            produced_count=len(produced),
            targets=[item.inputs[0]["target"] for item in produced if item.inputs],
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
    execution_ipc: ExecutionIpcTransportService = inject.service(ExecutionIpcTransportService)
    session_state: LeafRuntimeSessionStateService = inject.service(LeafRuntimeSessionStateService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)
    # Stream leaf boundary outputs to root in small chunks instead of one giant batch.
    boundary_stream_batch_max_items: int = 1

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafBoundaryExecuteCommand):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.boundary_execute.received",
            node_name="system.cp.leaf_boundary_execute",
            request_id=payload.request_id,
            input_count=len(payload.inputs),
            finalize=payload.finalize,
        )
        session = _leaf_session_from_ctx(ctx, payload=payload, session_state=self.session_state)
        tombstone_input = any(_leaf_boundary_input_tombstone(item) for item in payload.inputs)
        streamed_count = 0

        def _stream_chunk(chunk: list[object]) -> bool:
            nonlocal streamed_count
            envelopes = [item for item in chunk if isinstance(item, Envelope)]
            if not envelopes:
                return True
            event = ControlPlaneLeafBoundaryResultEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.request_id,
                status="partial",
                outputs=tuple(envelopes),
                tombstone_input=tombstone_input,
                tombstone_output=any(item.tombstone for item in envelopes),
            )
            sent = self._send_boundary_result(session=session, event=event)
            if sent:
                streamed_count += len(envelopes)
                _emit_leaf_debug(
                    self.debug_logging,
                    event="leaf.node.boundary_execute.chunk_sent",
                    node_name="system.cp.leaf_boundary_execute",
                    request_id=payload.request_id,
                    chunk_size=len(envelopes),
                    streamed_count=streamed_count,
                )
            return sent
        try:
            outputs = tuple(
                self._execute_boundary(
                    ctx=ctx,
                    session=session,
                    payload=payload,
                    stream_callback=_stream_chunk if payload.finalize else None,
                )
            )
            envelope_tombstone_output = any(
                isinstance(item, Envelope) and item.tombstone
                for item in outputs
            )
            # Terminal groups may consume tombstone at sink and emit no further
            # envelopes. Treat that as completed tombstone propagation.
            tombstone_output = bool(envelope_tombstone_output or (tombstone_input and not outputs))
            if not payload.finalize:
                _emit_leaf_debug(
                    self.debug_logging,
                    event="leaf.node.boundary_execute.background_completed",
                    node_name="system.cp.leaf_boundary_execute",
                    request_id=payload.request_id,
                    output_count=len(outputs),
                )
                return []
            boundary_result = ControlPlaneLeafBoundaryResultEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.request_id,
                status="completed",
                outputs=outputs,
                tombstone_input=tombstone_input,
                tombstone_output=tombstone_output,
            )
            produced: list[object] = [boundary_result]
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.boundary_execute.completed",
                node_name="system.cp.leaf_boundary_execute",
                request_id=payload.request_id,
                output_count=len(outputs),
                streamed_count=streamed_count,
                tombstone_input=tombstone_input,
                tombstone_output=tombstone_output,
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
                _emit_leaf_debug(
                    self.debug_logging,
                    event="leaf.node.boundary_execute.background_failed",
                    node_name="system.cp.leaf_boundary_execute",
                    request_id=payload.request_id,
                    error=exc.__class__.__name__,
                )
                return []
            _emit_leaf_debug(
                self.debug_logging,
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
                    tombstone_input=any(_leaf_boundary_input_tombstone(item) for item in payload.inputs),
                )
            ]

    def _execute_boundary(
        self,
        *,
        ctx: object | None,
        session: object,
        payload: ControlPlaneLeafBoundaryExecuteCommand,
        stream_callback: object | None,
    ) -> list[object]:
        stream_batch_max_items = self._resolve_stream_batch_max_items(ctx=ctx)
        execute_fn = getattr(self.boundary_execution, "execute")
        supports_streaming = False
        try:
            signature = inspect.signature(execute_fn)
            supports_streaming = (
                "stream_callback" in signature.parameters
                and "stream_batch_max_items" in signature.parameters
            )
        except (TypeError, ValueError):
            supports_streaming = False
        if supports_streaming:
            return list(
                execute_fn(
                    session=session,
                    inputs=list(payload.inputs),
                    finalize_runtime=bool(payload.finalize),
                    stream_callback=stream_callback,
                    stream_batch_max_items=stream_batch_max_items,
                )
            )
        return list(
            execute_fn(
                session=session,
                inputs=list(payload.inputs),
                finalize_runtime=bool(payload.finalize),
            )
        )

    def _resolve_stream_batch_max_items(self, *, ctx: object | None) -> int:
        runtime = _leaf_runtime_from_ctx(ctx)
        if isinstance(runtime, dict):
            platform = runtime.get("platform", {})
            if isinstance(platform, dict):
                boundary_dispatch = platform.get("boundary_dispatch", {})
                if isinstance(boundary_dispatch, dict):
                    value = boundary_dispatch.get("stream_batch_max_items")
                    if isinstance(value, int) and value > 0:
                        return int(value)
        if isinstance(self.boundary_stream_batch_max_items, int) and self.boundary_stream_batch_max_items > 0:
            return int(self.boundary_stream_batch_max_items)
        return 1

    def _send_boundary_result(
        self,
        *,
        session: object,
        event: ControlPlaneLeafBoundaryResultEvent,
    ) -> bool:
        ipc = self._resolve_execution_ipc_service(session)
        if ipc is None:
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.boundary_execute.chunk_send_no_ipc",
                node_name="system.cp.leaf_boundary_execute",
                request_id=event.request_id,
                output_count=len(event.outputs),
            )
            return False
        try:
            ipc.send(
                compose_execution_ipc_worker_target_id(event.worker_id, lane=EXECUTION_IPC_LANE_DATA),
                event,
                no_reply=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - preserve chunk for final result fallback.
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.boundary_execute.chunk_send_failed",
                node_name="system.cp.leaf_boundary_execute",
                request_id=event.request_id,
                output_count=len(event.outputs),
                error=exc.__class__.__name__,
            )
            return False

    def _resolve_execution_ipc_service(self, session: object) -> ExecutionIpcTransportService | None:
        candidate = self.execution_ipc
        if isinstance(candidate, ExecutionIpcTransportService):
            return candidate
        if callable(getattr(candidate, "send", None)) and callable(getattr(candidate, "recv", None)):
            return candidate  # type: ignore[return-value]
        child = getattr(session, "child", None)
        scope = getattr(child, "scenario_scope", None)
        resolve = getattr(scope, "resolve", None)
        if not callable(resolve):
            return None
        try:
            service = resolve("service", ExecutionIpcTransportService)
        except Exception:
            return None
        if isinstance(service, ExecutionIpcTransportService):
            return service
        if callable(getattr(service, "send", None)) and callable(getattr(service, "recv", None)):
            return service  # type: ignore[return-value]
        return None


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
    runner_control: LeafRunnerControlService = inject.service(LeafRunnerControlService)
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStopCommand):
            return []
        _emit_leaf_debug(
            self.debug_logging,
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
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.stop.ack_produced",
            node_name="system.cp.leaf_stop",
            command_id=payload.command_id,
        )
        self.runner_control.request_stop()
        return produced


def _leaf_session_from_ctx(
    ctx: object | None,
    *,
    payload: object,
    session_state: LeafRuntimeSessionStateService | None = None,
) -> object:
    if isinstance(ctx, dict):
        candidate = ctx.get("__leaf_session")
        if candidate is not None:
            return candidate
    if session_state is not None:
        current = session_state.current_session()
        if current is not None:
            return current
    group_name = getattr(payload, "target_group", "worker")
    worker_id = getattr(payload, "worker_id", f"{group_name}#1")
    return SimpleNamespace(
        child=None,
        group_name=group_name,
        worker_id=worker_id,
        runner_profile_requested="async",
        runner_profile_effective="async",
    )


def _leaf_worker_id_from_env() -> str:
    worker_id = os.environ.get("STREAM_KERNEL_WORKER_ID")
    if isinstance(worker_id, str) and worker_id:
        return worker_id
    return "worker#1"


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


def _leaf_boundary_input_tombstone(item: object) -> bool:
    if isinstance(item, dict):
        return item.get("tombstone") is True
    return getattr(item, "tombstone", None) is True


def _runtime_node_names_from_ctx(
    ctx: object | None,
    *,
    session_state: LeafRuntimeSessionStateService | None = None,
) -> set[str]:
    session = None
    if not isinstance(ctx, dict):
        session = None
    else:
        session = ctx.get("__leaf_session")
    if session is None and session_state is not None:
        session = session_state.current_session()
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
    if node_name.startswith("system.debug."):
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
    "LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME",
    "LEAF_COMMAND_INGRESS_SOURCE_NODE_PREFIX",
    "leaf_command_ingress_source_node_name",
    "leaf_command_ingress_source_lanes",
    "is_leaf_command_ingress_source_node_name",
    "ControlPlaneLeafCommandIngressSourceNode",
    "ControlPlaneLeafReplyDispatchNode",
    "ControlPlaneLeafApplyConfigNode",
    "ControlPlaneLeafDiscoveryRequestNode",
    "ControlPlaneLeafSnapshotApplyNode",
    "ControlPlaneLeafStartWorkNode",
    "ControlPlaneLeafBoundaryExecuteNode",
    "ControlPlaneLeafTombstoneFinalizeNode",
    "ControlPlaneLeafBootstrapDispatchNode",
    "ControlPlaneLeafBootstrapNode",
    "ControlPlaneLeafConfigApplyRuntimeNode",
    "ControlPlaneLeafStopNode",
]
