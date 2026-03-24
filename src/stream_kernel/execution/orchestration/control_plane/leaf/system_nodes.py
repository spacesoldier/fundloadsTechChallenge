from __future__ import annotations

import asyncio
import inspect
import os
import time
from dataclasses import dataclass, field
from threading import Lock
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
)
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
from stream_kernel.integration.work_queue import QueuePort
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
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafRunnerTombstoneEvent,
    ControlPlaneLeafSinkDispatchAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
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
from stream_kernel.routing.envelope import Envelope

LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME = "source:system.cp.command_ingress"
LEAF_COMMAND_INGRESS_SOURCE_NODE_PREFIX = f"{LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME}:"
LEAF_RUNTIME_INGRESS_SOURCE_NODE_NAME = "source:system.ipc.ingress"
LEAF_RUNTIME_INGRESS_SOURCE_NODE_PREFIX = f"{LEAF_RUNTIME_INGRESS_SOURCE_NODE_NAME}:"


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
    return (EXECUTION_IPC_LANE_CONTROL,)


def leaf_runtime_ingress_source_node_name(*, lane: str) -> str:
    lane_name = lane if isinstance(lane, str) and lane else EXECUTION_IPC_LANE_DATA
    return f"{LEAF_RUNTIME_INGRESS_SOURCE_NODE_PREFIX}{lane_name}"


def is_leaf_runtime_ingress_source_node_name(node_name: object) -> bool:
    if not isinstance(node_name, str) or not node_name:
        return False
    return node_name == LEAF_RUNTIME_INGRESS_SOURCE_NODE_NAME or node_name.startswith(
        LEAF_RUNTIME_INGRESS_SOURCE_NODE_PREFIX
    )


def leaf_runtime_ingress_source_lanes() -> tuple[str, ...]:
    return (
        EXECUTION_IPC_LANE_DATA,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )


def leaf_runtime_ingress_drain_source_node_name() -> str:
    return leaf_runtime_ingress_source_node_name(lane="drain")


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
        BootstrapControl,
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
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)
    lane: str = EXECUTION_IPC_LANE_CONTROL
    poll_lanes: tuple[str, ...] = ()
    poll_worker_ids: tuple[str, ...] = ()
    poll_worker_ids_by_lane: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source_name: str = LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME
    poll_interval_seconds: float = 0.01
    max_messages_per_poll: int = 64
    work_queue: object = inject.queue(Envelope, qualifier="execution.asyncio")
    _scheduler_registered: bool = field(default=False, init=False, repr=False)
    _wakeup_registered: bool = field(default=False, init=False, repr=False)
    _wakeup_pending: bool = field(default=False, init=False, repr=False)
    _wakeup_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _poll_lane_index: int = field(default=0, init=False, repr=False)
    _poll_worker_index_by_lane: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def initialize(self) -> list[object]:
        self._register_data_wakeup()
        if self._scheduler_registered:
            return []
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME
        )
        self._scheduler_registered = True
        # Push-model bootstrap: one initial pulse is enough, next pulses are produced
        # by transport data-available callbacks (and explicit self-rearm when drained
        # exactly to budget boundary).
        return [BootstrapControl(target=source_name)]

    async def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, BootstrapControl):
            return []
        if payload.target != self.source_name:
            return []
        # Retry callback registration on each pulse in case endpoint binding
        # happened after initialize() or registration failed transiently.
        self._register_data_wakeup()
        self._clear_wakeup_pending()
        if self.runner_control.stop_requested():
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.command_source.stop_requested",
                node_name=self.source_name,
                lane=self.lane,
            )
            return []
        poll_timeout_seconds = 0.0
        poll_for_lane_async = getattr(self.ingress, "poll_next_message_for_lane_async", None)
        poll_for_lane = getattr(self.ingress, "poll_next_message_for_lane", None)
        lane_specs = self._poll_lane_specs()
        if not lane_specs:
            return []
        budget = max(1, int(self.max_messages_per_poll))
        produced: list[object] = []
        had_polled_payload = False
        for _ in range(budget):
            polled = await self._poll_one_message(
                lane_specs=lane_specs,
                poll_for_lane_async=poll_for_lane_async,
                poll_for_lane=poll_for_lane,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if polled is None:
                break
            had_polled_payload = True
            lane_name, chosen_worker_id, next_message = polled
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.command_source.message_polled",
                node_name=self.source_name,
                lane=lane_name,
                polled_worker_id=chosen_worker_id,
                payload_type=type(next_message).__name__,
            )
            normalized = self._normalize_polled_message(
                message=next_message,
                lane=lane_name,
                polled_worker_id=chosen_worker_id,
                ctx=ctx,
            )
            if normalized is None:
                continue
            produced.append(normalized)
        if len(produced) >= budget:
            produced.append(BootstrapControl(target=self.source_name))
        elif not produced and not had_polled_payload and not self._wakeup_registered:
            # Keep a lightweight retry pulse alive when wakeup callbacks are not
            # available yet; otherwise control-lane startup commands can stall.
            produced.append(BootstrapControl(target=self.source_name))
        return produced

    def _normalize_polled_message(
        self,
        *,
        message: object,
        lane: str,
        polled_worker_id: str,
        ctx: object | None,
    ) -> object | None:
        if is_leaf_runtime_ingress_source_node_name(self.source_name):
            wrapped = _leaf_runtime_ingress_boundary_command(
                message=message,
                lane=lane,
                polled_worker_id=polled_worker_id,
                ctx=ctx,
            )
            if wrapped is not None:
                return wrapped
        return message

    def _poll_lane_specs(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        lanes = tuple(
            lane.strip().lower()
            for lane in self.poll_lanes
            if isinstance(lane, str) and lane.strip()
        )
        if not lanes:
            lanes = (self.lane if isinstance(self.lane, str) and self.lane else EXECUTION_IPC_LANE_CONTROL,)
        resolved: list[tuple[str, tuple[str, ...]]] = []
        for lane in lanes:
            worker_ids = self._poll_worker_ids_for_lane(lane)
            if not worker_ids:
                continue
            resolved.append((lane, worker_ids))
        return tuple(resolved)

    def _poll_worker_ids_for_lane(self, lane: str) -> tuple[str, ...]:
        mapped = self.poll_worker_ids_by_lane.get(lane)
        if isinstance(mapped, tuple):
            configured = tuple(
                worker_id
                for worker_id in mapped
                if isinstance(worker_id, str) and worker_id
            )
            if configured:
                return configured
        return self._poll_worker_ids()

    def _poll_worker_ids(self) -> tuple[str, ...]:
        configured = tuple(
            worker_id
            for worker_id in self.poll_worker_ids
            if isinstance(worker_id, str) and worker_id
        )
        if configured:
            return configured
        return (_leaf_worker_id_from_env(),)

    def _round_robin_worker_ids(self, lane: str, worker_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not worker_ids:
            return ()
        size = len(worker_ids)
        start = self._poll_worker_index_by_lane.get(lane, 0) % size
        ordered = tuple(worker_ids[(start + offset) % size] for offset in range(size))
        self._poll_worker_index_by_lane[lane] = (start + 1) % size
        return ordered

    async def _poll_one_message(
        self,
        *,
        lane_specs: tuple[tuple[str, tuple[str, ...]], ...],
        poll_for_lane_async: object,
        poll_for_lane: object,
        poll_timeout_seconds: float,
    ) -> tuple[str, str, object] | None:
        if not lane_specs:
            return None
        size = len(lane_specs)
        start_lane = self._poll_lane_index % size
        for lane_offset in range(size):
            index = (start_lane + lane_offset) % size
            lane_name, worker_ids = lane_specs[index]
            for worker_id in self._round_robin_worker_ids(lane_name, worker_ids):
                if callable(poll_for_lane_async):
                    next_message = await poll_for_lane_async(
                        worker_id=worker_id,
                        lane=lane_name,
                        timeout_seconds=poll_timeout_seconds,
                    )
                elif callable(poll_for_lane):
                    next_message = poll_for_lane(
                        worker_id=worker_id,
                        lane=lane_name,
                        timeout_seconds=poll_timeout_seconds,
                    )
                else:
                    next_message = self.ingress.poll_next_message(worker_id=worker_id)
                if next_message is None:
                    continue
                self._poll_lane_index = (index + 1) % size
                return (lane_name, worker_id, next_message)
        self._poll_lane_index = (start_lane + 1) % size
        return None

    def _register_data_wakeup(self) -> None:
        if self._wakeup_registered:
            return
        loop: object | None = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        register = getattr(self.ingress, "register_data_available_callback", None)
        if not callable(register):
            return
        lane_specs = self._poll_lane_specs()
        if not lane_specs:
            return
        registered_any = False
        for lane_name, worker_ids in lane_specs:
            for worker_id in worker_ids:
                try:
                    result = register(
                        worker_id=worker_id,
                        lane=lane_name,
                        callback=self._on_data_available,
                        loop=loop,
                    )
                    if result is False:
                        continue
                    registered_any = True
                except Exception:
                    continue
        self._wakeup_registered = registered_any

    def _on_data_available(self) -> None:
        queue = self._queue_port()
        if queue is None:
            return
        source_name = (
            self.source_name
            if isinstance(self.source_name, str) and self.source_name
            else LEAF_COMMAND_INGRESS_SOURCE_NODE_NAME
        )
        with self._wakeup_lock:
            if self._wakeup_pending:
                return
            self._wakeup_pending = True
        envelope = Envelope(
            payload=BootstrapControl(target=source_name),
            target=source_name,
        )
        try:
            queue.push(envelope)
        except Exception:
            self._clear_wakeup_pending()

    def _clear_wakeup_pending(self) -> None:
        with self._wakeup_lock:
            self._wakeup_pending = False

    def _queue_port(self) -> QueuePort | None:
        candidate = self.work_queue
        if isinstance(candidate, QueuePort):
            return candidate
        if callable(getattr(candidate, "push", None)):
            return candidate  # type: ignore[return-value]
        return None


@node(
    name="system.cp.leaf_reply_dispatch",
    consumes=[
        ControlPlaneLeafHelloEvent,
        ControlPlaneLeafDiscoveryAckEvent,
        ControlPlaneLeafConfigAckEvent,
        ControlPlaneLeafReplyDispatchDiagEvent,
        ControlPlaneLeafBoundaryOutputsEvent,
        ControlPlaneLeafDrainReadyEvent,
        ControlPlaneLeafStopAckEvent,
    ],
    emits=[ControlPlaneLeafSinkDispatchAckEvent],
)
@dataclass
class ControlPlaneLeafReplyDispatchNode:
    reply_dispatch: LeafControlReplyDispatchService = inject.service(LeafControlReplyDispatchService)
    handoff_dispatch: ExecutionIpcHandoffDispatchService = inject.service(
        ExecutionIpcHandoffDispatchService
    )
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(
            payload,
            (
                ControlPlaneLeafHelloEvent,
                ControlPlaneLeafDiscoveryAckEvent,
                ControlPlaneLeafConfigAckEvent,
                ControlPlaneLeafReplyDispatchDiagEvent,
                ControlPlaneLeafBoundaryOutputsEvent,
                ControlPlaneLeafDrainReadyEvent,
                ControlPlaneLeafStopAckEvent,
            ),
        ):
            return []
        worker_id = payload.worker_id
        if not isinstance(worker_id, str) or not worker_id:
            worker_id = _leaf_worker_id_from_env()
        if not isinstance(payload, ControlPlaneLeafBoundaryOutputsEvent):
            accepted = self.reply_dispatch.dispatch_reply(worker_id=worker_id, payload=payload)
            diag = _leaf_reply_dispatch_diag_for_payload(payload=payload, accepted=accepted)
            if not accepted:
                if isinstance(payload, ControlPlaneLeafDrainReadyEvent):
                    _emit_leaf_debug(
                        self.debug_logging,
                        event="leaf.node.reply_dispatch.drain_ready_failed",
                        node_name="system.cp.leaf_reply_dispatch",
                        worker_id=worker_id,
                        target_group=payload.target_group,
                        request_id=payload.request_id,
                    )
            if diag is not None:
                self.reply_dispatch.dispatch_reply(worker_id=worker_id, payload=diag)
            return []
        produced: list[object] = []
        all_required_dispatched = True
        source_group = worker_id.rsplit("#", 1)[0] if "#" in worker_id else None
        for output in payload.outputs:
            if isinstance(output, Envelope):
                accepted = self._dispatch_boundary_envelope(
                    envelope=output,
                    source_group=source_group,
                )
            else:
                accepted = self.reply_dispatch.dispatch_reply(worker_id=worker_id, payload=output)
            if not accepted:
                if not _leaf_can_ignore_dispatch_failure(output):
                    all_required_dispatched = False
        if all_required_dispatched:
            ack = _leaf_sink_dispatch_ack(payload)
            if ack is not None:
                produced.append(ack)
        return produced

    def _dispatch_boundary_envelope(
        self,
        *,
        envelope: Envelope,
        source_group: str | None,
    ) -> bool:
        if not isinstance(envelope.target, str) or not envelope.target:
            return False
        dispatch = getattr(self.handoff_dispatch, "dispatch_envelope", None)
        if not callable(dispatch):
            return False
        try:
            return bool(dispatch(envelope, source_group=source_group))
        except TypeError:
            return bool(dispatch(envelope))
        except Exception:
            return False


@node(
    name="system.cp.leaf_source_poll_from_sink_ack",
    consumes=[ControlPlaneLeafSinkDispatchAckEvent],
    emits=[ControlPlaneLeafBoundaryExecuteCommand],
)
@dataclass
class ControlPlaneLeafSourcePollFromSinkAckNode:
    def __call__(self, msg: object, ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafSinkDispatchAckEvent):
            return []
        next_command = _leaf_next_source_poll_command(payload, ctx=ctx)
        if next_command is None:
            return []
        return [next_command]


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
    emits=[ControlPlaneLeafDiscoveryAckEvent, ControlPlaneLeafReplyDispatchDiagEvent],
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
        return [
            result,
            ControlPlaneLeafReplyDispatchDiagEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.request_id,
                stage="leaf_snapshot_apply",
                payload_type=type(result).__name__,
                status="produced",
                detail=getattr(result, "status", None),
            ),
        ]


@node(
    name="system.cp.leaf_apply_config",
    consumes=[ControlPlaneLeafConfigCardEvent],
    emits=[ControlPlaneLeafConfigAckEvent, ControlPlaneLeafReplyDispatchDiagEvent],
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
        return [
            result,
            ControlPlaneLeafReplyDispatchDiagEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.config_id,
                stage="leaf_apply_config",
                payload_type=type(result).__name__,
                status="produced",
                detail=result.status,
            ),
        ]


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
        policy = _leaf_source_ingress_policy(ctx=ctx)
        session_group = getattr(session, "group_name", None)
        if not isinstance(session_group, str) or not session_group:
            session_group = os.environ.get("STREAM_KERNEL_PROCESS_GROUP") or "worker"
        session_worker_id = getattr(session, "worker_id", None)
        if not isinstance(session_worker_id, str) or not session_worker_id:
            session_worker_id = _leaf_worker_id_from_env()
        produced: list[ControlPlaneLeafBoundaryExecuteCommand] = []
        for index, target in enumerate(unique_targets):
            command = _leaf_make_source_poll_command(
                target_group=session_group,
                worker_id=session_worker_id,
                source_target=target,
                single_shot=policy.single_shot,
                batch_size=policy.batch_size,
                request_id_prefix="start-work",
                request_index=index + 1,
            )
            if command is not None:
                produced.append(command)
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
    emits=[ControlPlaneLeafBoundaryOutputsEvent, ControlPlaneLeafRunnerTombstoneEvent],
)
@dataclass
class ControlPlaneLeafBoundaryExecuteNode:
    boundary_execution: LeafBoundaryExecutionService = inject.service(LeafBoundaryExecutionService)
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
        source_target = _leaf_boundary_source_target(payload.inputs)
        try:
            outputs = tuple(
                self._execute_boundary(
                    ctx=ctx,
                    session=session,
                    payload=payload,
                    stream_callback=None,
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
            boundary_outputs = ControlPlaneLeafBoundaryOutputsEvent(
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.request_id,
                outputs=outputs,
                source_target=source_target,
                tombstone_input=tombstone_input,
                tombstone_output=tombstone_output,
            )
            produced: list[object] = [boundary_outputs]
            if tombstone_output:
                produced.append(
                    ControlPlaneLeafRunnerTombstoneEvent(
                        target_group=payload.target_group,
                        worker_id=payload.worker_id,
                        request_id=(
                            "runner-tombstone:"
                            f"{payload.worker_id}:{payload.request_id}:system.cp.leaf_boundary_execute"
                        ),
                        observed_node="system.cp.leaf_boundary_execute",
                        expected_nodes=("system.cp.leaf_boundary_execute",),
                        tombstone_output=True,
                    )
                )
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.boundary_execute.completed",
                node_name="system.cp.leaf_boundary_execute",
                request_id=payload.request_id,
                output_count=len(outputs),
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
            return []

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


@node(
    name="system.cp.leaf_tombstone_finalize",
    consumes=[ControlPlaneLeafRunnerTombstoneEvent],
    emits=[ControlPlaneLeafDrainReadyEvent],
)
@dataclass
class ControlPlaneLeafTombstoneFinalizeNode:
    readiness: ControlPlaneLeafShutdownReadinessService = inject.service(
        ControlPlaneLeafShutdownReadinessService
    )
    debug_logging: object | None = inject.service(LeafLifecycleDebugLoggingService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafRunnerTombstoneEvent):
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.tombstone_finalize.received",
            node_name="system.cp.leaf_tombstone_finalize",
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            request_id=payload.request_id,
            observed_node=payload.observed_node,
            expected_nodes=list(payload.expected_nodes),
            tombstone_output=payload.tombstone_output,
        )
        event = self.readiness.observe_runner_tombstone(payload)
        if event is None:
            _emit_leaf_debug(
                self.debug_logging,
                event="leaf.node.tombstone_finalize.pending",
                node_name="system.cp.leaf_tombstone_finalize",
                target_group=payload.target_group,
                worker_id=payload.worker_id,
                request_id=payload.request_id,
                observed_node=payload.observed_node,
            )
            return []
        _emit_leaf_debug(
            self.debug_logging,
            event="leaf.node.tombstone_finalize.ready_emitted",
            node_name="system.cp.leaf_tombstone_finalize",
            target_group=event.target_group,
            worker_id=event.worker_id,
            request_id=event.request_id,
            tombstone_output=event.tombstone_output,
        )
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
        produced: list[object] = [
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


def _leaf_runtime_ingress_boundary_command(
    *,
    message: object,
    lane: str,
    polled_worker_id: str,
    ctx: object | None,
) -> ControlPlaneLeafBoundaryExecuteCommand | None:
    if not isinstance(message, Envelope):
        return None
    target = message.target
    if not isinstance(target, str) or not target:
        return None
    runtime = _leaf_runtime_from_ctx(ctx)
    target_group = _leaf_target_group(runtime)
    runtime_worker_id = _leaf_worker_id(runtime=runtime, target_group=target_group)
    worker_id = runtime_worker_id
    if not isinstance(worker_id, str) or not worker_id:
        worker_id = polled_worker_id if isinstance(polled_worker_id, str) and polled_worker_id else _leaf_worker_id_from_env()
    request_id = f"runtime-ingress:{worker_id}:{lane}:{time.time_ns()}"
    return ControlPlaneLeafBoundaryExecuteCommand(
        target_group=target_group,
        worker_id=worker_id,
        request_id=request_id,
        inputs=(
            {
                "dispatch_group": target_group,
                "target": target,
                "payload": message.payload,
                "trace_id": message.trace_id,
                "reply_to": message.reply_to,
                "span_id": message.span_id,
                "tombstone": bool(message.tombstone),
            },
        ),
        finalize=True,
    )


def _leaf_worker_id_from_env() -> str:
    worker_id = os.environ.get("STREAM_KERNEL_WORKER_ID")
    if isinstance(worker_id, str) and worker_id:
        return worker_id
    return "worker#1"


def _leaf_boundary_source_target(inputs: tuple[object, ...]) -> str | None:
    if not isinstance(inputs, tuple) or not inputs:
        return None
    resolved_target: str | None = None
    for item in inputs:
        if not isinstance(item, dict):
            return None
        target = item.get("target")
        payload = item.get("payload")
        if not (
            isinstance(target, str)
            and target.startswith("source:")
            and isinstance(payload, BootstrapControl)
            and payload.target == target
        ):
            return None
        if resolved_target is None:
            resolved_target = target
            continue
        if target != resolved_target:
            return None
    return resolved_target


def _leaf_next_source_poll_command(
    payload: ControlPlaneLeafSinkDispatchAckEvent,
    *,
    ctx: object | None,
) -> ControlPlaneLeafBoundaryExecuteCommand | None:
    if payload.tombstone_output:
        return None
    policy = _leaf_source_ingress_policy(ctx=ctx)
    if policy.advance_signal != "sink_dispatch_ack":
        return None
    if not policy.single_shot:
        # all-at-once mode drains source using source self-rearm; no ack-driven polls.
        return None
    source_target = payload.source_target
    if not isinstance(source_target, str) or not source_target:
        return None
    return _leaf_make_source_poll_command(
        target_group=payload.target_group,
        worker_id=payload.worker_id,
        source_target=source_target,
        single_shot=policy.single_shot,
        batch_size=policy.batch_size,
        request_id_prefix="source-poll",
        request_index=1,
    )


@dataclass(frozen=True, slots=True)
class _LeafSourceIngressPolicy:
    single_shot: bool
    batch_size: int
    advance_signal: str


def _leaf_source_ingress_policy(*, ctx: object | None) -> _LeafSourceIngressPolicy:
    runtime = _leaf_runtime_from_ctx(ctx)
    platform = runtime.get("platform") if isinstance(runtime, dict) else None
    source_ingress = platform.get("source_ingress") if isinstance(platform, dict) else None
    if not isinstance(source_ingress, dict):
        return _LeafSourceIngressPolicy(single_shot=True, batch_size=1, advance_signal="sink_dispatch_ack")
    pacing_mode = source_ingress.get("pacing_mode", "batch")
    if isinstance(pacing_mode, str):
        pacing_mode = pacing_mode.strip().lower()
    else:
        pacing_mode = "batch"
    batch_size = source_ingress.get("batch_size", 1)
    if not isinstance(batch_size, int) or batch_size <= 0:
        batch_size = 1
    advance_signal = source_ingress.get("advance_signal", "sink_dispatch_ack")
    if isinstance(advance_signal, str):
        advance_signal = advance_signal.strip().lower()
    else:
        advance_signal = "sink_dispatch_ack"
    if pacing_mode == "all":
        return _LeafSourceIngressPolicy(single_shot=False, batch_size=1, advance_signal=advance_signal)
    return _LeafSourceIngressPolicy(
        single_shot=True,
        batch_size=max(1, int(batch_size)),
        advance_signal=advance_signal,
    )


def _leaf_make_source_poll_command(
    *,
    target_group: str,
    worker_id: str,
    source_target: str,
    single_shot: bool,
    batch_size: int,
    request_id_prefix: str,
    request_index: int,
) -> ControlPlaneLeafBoundaryExecuteCommand | None:
    if not isinstance(source_target, str) or not source_target:
        return None
    count = 1 if not single_shot else max(1, int(batch_size))
    inputs: list[dict[str, object]] = []
    for _ in range(count):
        inputs.append(
            {
                "dispatch_group": target_group,
                "target": source_target,
                "payload": BootstrapControl(target=source_target, single_shot=single_shot),
                "trace_id": None,
                "reply_to": None,
                "span_id": None,
                "tombstone": False,
            }
        )
    request_id = f"{request_id_prefix}:{worker_id}:{source_target}:{time.time_ns()}:{max(1, int(request_index))}"
    return ControlPlaneLeafBoundaryExecuteCommand(
        target_group=target_group,
        worker_id=worker_id,
        request_id=request_id,
        inputs=tuple(inputs),
        # Source-ingress pacing depends on boundary outputs for sink ACK feedback.
        # Keep finalization enabled so each poll cycle emits boundary outputs event.
        finalize=True,
    )


def _leaf_sink_dispatch_ack(
    payload: ControlPlaneLeafBoundaryOutputsEvent,
) -> ControlPlaneLeafSinkDispatchAckEvent | None:
    source_target = payload.source_target
    if not isinstance(source_target, str) or not source_target:
        return None
    payload_class = _leaf_payload_class_name(payload.outputs)
    return ControlPlaneLeafSinkDispatchAckEvent(
        target_group=payload.target_group,
        worker_id=payload.worker_id,
        request_id=payload.request_id,
        source_target=source_target,
        payload_class=payload_class,
        tombstone_output=payload.tombstone_output,
    )


def _leaf_reply_dispatch_diag_for_payload(
    *,
    payload: object,
    accepted: bool,
) -> ControlPlaneLeafReplyDispatchDiagEvent | None:
    if isinstance(payload, ControlPlaneLeafReplyDispatchDiagEvent):
        return None
    if isinstance(payload, ControlPlaneLeafDiscoveryAckEvent):
        return ControlPlaneLeafReplyDispatchDiagEvent(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            request_id=payload.request_id,
            stage="leaf_reply_dispatch",
            payload_type=type(payload).__name__,
            status="accepted" if accepted else "rejected",
            detail=payload.status,
        )
    if isinstance(payload, ControlPlaneLeafConfigAckEvent):
        return ControlPlaneLeafReplyDispatchDiagEvent(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            request_id=payload.config_id,
            stage="leaf_reply_dispatch",
            payload_type=type(payload).__name__,
            status="accepted" if accepted else "rejected",
            detail=payload.status,
        )
    return None


def _leaf_payload_class_name(outputs: tuple[object, ...]) -> str:
    if not isinstance(outputs, tuple) or not outputs:
        return "empty"
    first = outputs[0]
    if isinstance(first, Envelope):
        return type(first.payload).__name__
    return type(first).__name__


def _leaf_can_ignore_dispatch_failure(output: object) -> bool:
    if not isinstance(output, Envelope):
        return False
    target = output.target
    target_names: tuple[str, ...]
    if isinstance(target, str) and target:
        target_names = (target,)
    elif isinstance(target, tuple):
        target_names = tuple(name for name in target if isinstance(name, str) and name)
    else:
        return False
    if not target_names:
        return False
    for name in target_names:
        if name.startswith("system.obs."):
            continue
        if name.startswith("system.debug."):
            continue
        if name.startswith("system.transport.handoff."):
            continue
        return False
    return True


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
    result = dict(runtime) if isinstance(runtime, dict) else {}
    if "__process_group" not in result:
        process_group = getattr(child, "process_group", None)
        if not (isinstance(process_group, str) and process_group):
            process_group = getattr(session, "group_name", None)
        if isinstance(process_group, str) and process_group:
            result["__process_group"] = process_group
    if "__worker_id" not in result:
        worker_id = getattr(session, "worker_id", None)
        if isinstance(worker_id, str) and worker_id:
            result["__worker_id"] = worker_id
    return result


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
    "leaf_runtime_ingress_source_node_name",
    "leaf_runtime_ingress_source_lanes",
    "leaf_runtime_ingress_drain_source_node_name",
    "is_leaf_runtime_ingress_source_node_name",
    "ControlPlaneLeafCommandIngressSourceNode",
    "ControlPlaneLeafReplyDispatchNode",
    "ControlPlaneLeafSourcePollFromSinkAckNode",
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
