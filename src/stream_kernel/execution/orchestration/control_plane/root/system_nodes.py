from __future__ import annotations

import time
from dataclasses import dataclass

from stream_kernel.application_context.inject import inject
from stream_kernel.execution.orchestration.control_plane.root.channel_services import (
    ControlPlaneRootRunnerControlService,
)
from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_contract import (
    ControlPlaneRootRuntimeBootstrapService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    ExecutionIpcKvStreamPort,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import LogDispatchEvent
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneConfigStreamService,
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_config_apply import (
    ControlPlaneConfigApplyTrackerService,
    ControlPlaneNodeConfigApplyService,
    ControlPlaneObservabilityConfigApplyService,
    ControlPlaneSystemConfigApplyService,
    resolve_expected_config_apply_counts,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    ControlPlaneDagAssemblyService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_materialization import (
    ControlPlaneDiscoveryMaterializationService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneConfigApplyCompletedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    ExecutionGroupConfigRecord,
    ControlPlaneGroupSpec,
    ControlPlaneInitEvent,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneStartWorkEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneRootLeafStartWorkCommand,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
    ControlPlaneRootLeafBoundaryExecuteRequestEvent,
    ControlPlaneRootLeafStopRequestEvent,
    ControlPlaneRootPulse,
    ControlPlaneShutdownReadyEvent,
    ControlPlaneSpawnRequestedEvent,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
    ControlPlaneNodeConfigAppliedEvent,
    ControlPlaneObservabilityConfigAppliedEvent,
    ControlPlaneSystemConfigAppliedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
    ControlPlaneShutdownReadinessService,
)
from stream_kernel.platform.services.runtime.control_plane_ring_topology import (
    ControlPlaneRingTopologyService,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.routing.envelope import Envelope


@node(
    name="system.cp.dag_assembly",
    consumes=[ControlPlaneDagAssemblyRequestedEvent],
    emits=[ControlPlaneDagAssembledEvent],
)
@dataclass
class ControlPlaneDagAssemblyNode:
    assembly: ControlPlaneDagAssemblyService = inject.service(ControlPlaneDagAssemblyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDagAssemblyRequestedEvent):
            return []
        plan = None
        assemble = getattr(self.assembly, "assemble", None)
        if callable(assemble):
            plan = assemble(runtime=payload.runtime)
        if not isinstance(plan, ControlPlaneLaunchPlan) or not plan.groups:
            return []
        return [ControlPlaneDagAssembledEvent(runtime=payload.runtime, plan=plan)]


@node(
    name="system.cp.init_plan",
    consumes=[ControlPlaneDagAssembledEvent],
    emits=[
        ControlPlaneInitializationRequestedEvent,
        ControlPlaneLaunchPlanEvent,
        ControlPlaneSpawnRequestedEvent,
    ],
)
@dataclass
class ControlPlaneInitPlanNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    ring_topology: object | None = inject.service(ControlPlaneRingTopologyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDagAssembledEvent):
            return []
        plan_signature = _launch_plan_signature(payload.plan)
        for event in reversed(self.state.events()):
            if not isinstance(event, ControlPlaneLaunchPlanEvent):
                continue
            if _launch_plan_signature(event.plan) == plan_signature:
                # Idempotency guard: repeated startup pulses must not re-emit spawn requests.
                return []
        self._configure_ring_topology(payload)
        plan_event = ControlPlaneLaunchPlanEvent(plan=payload.plan)
        self.state.append_event(plan_event)
        spawn_events = [
            ControlPlaneSpawnRequestedEvent(
                group_name=group.group_name,
                workers=group.workers,
                nodes=tuple(group.nodes),
            )
            for group in payload.plan.groups
        ]
        return [
            plan_event,
            ControlPlaneInitializationRequestedEvent(runtime=payload.runtime),
            *spawn_events,
        ]

    def _configure_ring_topology(self, payload: ControlPlaneDagAssembledEvent) -> None:
        candidate = self.ring_topology
        configure = getattr(candidate, "configure", None)
        if not callable(configure):
            return
        groups = [
            {
                "name": group.group_name,
                "workers": int(group.workers),
                "nodes": list(group.nodes),
            }
            for group in payload.plan.groups
        ]
        try:
            configure(runtime=dict(payload.runtime), groups=groups)
        except Exception:
            return


@node(
    name="system.cp.start_work_dispatch",
    consumes=[ControlPlaneStartWorkEvent],
    emits=[LogMessage, ControlPlaneRootLeafStartWorkCommand],
)
@dataclass
class ControlPlaneStartWorkDispatchNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneStartWorkEvent):
            return []
        if any(isinstance(event, _StartWorkDispatchedMarker) for event in self.state.events()):
            return []
        source_targets = tuple(
            dict.fromkeys(
                payload.source_targets or _source_node_targets_from_state(self.state.events())
            )
        )
        launch_plan = _latest_launch_plan_from_state(self.state.events())
        if launch_plan is None:
            self.state.append_event(_StartWorkDispatchedMarker(worker_count=0))
            return [
                LogMessage(
                    level="warning",
                    message="control-plane start-work dispatch unavailable",
                    fields={
                        "event": "control_plane.runtime.start_work_dispatch_unavailable",
                        "source_targets": list(source_targets),
                        "launch_plan_available": False,
                        "worker_command_count": 0,
                    },
                )
            ]
        requested = set(payload.source_targets) if payload.source_targets else None
        command_seed = f"start-work:{int(time.time() * 1000)}"
        commands: list[ControlPlaneRootLeafStartWorkCommand] = []
        targeted_workers: list[str] = []
        for group in launch_plan.groups:
            group_sources = tuple(
                node_name
                for node_name in group.nodes
                if isinstance(node_name, str) and node_name.startswith("source:")
            )
            if requested is not None:
                group_sources = tuple(node for node in group_sources if node in requested)
            if not group_sources:
                continue
            worker_count = max(1, int(group.workers))
            for worker_slot in range(1, worker_count + 1):
                worker_id = f"{group.group_name}#{worker_slot}"
                targeted_workers.append(worker_id)
                commands.append(
                    ControlPlaneRootLeafStartWorkCommand(
                        target_group=group.group_name,
                        worker_id=worker_id,
                        source_targets=group_sources,
                        command_id=f"{command_seed}:{worker_id}",
                    )
                )
        self.state.append_event(_StartWorkDispatchedMarker(worker_count=len(commands)))
        level = "info" if commands else "warning"
        return [
            LogMessage(
                level=level,
                message="control-plane start-work commands enqueued",
                fields={
                    "event": "control_plane.runtime.start_work_commands_enqueued",
                    "command_seed": command_seed,
                    "worker_command_count": len(commands),
                    "target_workers": list(targeted_workers),
                    "source_targets": list(source_targets),
                },
            )
        ] + commands


@node(
    name="system.cp.start_work_readiness",
    consumes=[ControlPlaneLeafConfigAckEvent],
    emits=[ControlPlaneStartWorkEvent],
)
@dataclass
class ControlPlaneStartWorkReadinessNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    config_store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigAckEvent):
            return []
        if not _is_start_work_on_all_ready_enabled(self.config_store):
            return []
        events = self.state.events()
        if any(isinstance(event, _StartWorkDispatchedMarker) for event in events):
            return []
        expected_worker_ids = _expected_worker_ids_for_start_work(events)
        if not expected_worker_ids:
            return []
        latest_status = _latest_worker_statuses_for_start_work(
            events=events,
            expected_worker_ids=expected_worker_ids,
        )
        latest_status[payload.worker_id] = payload.status
        if any(latest_status.get(worker_id) == "rejected" for worker_id in expected_worker_ids):
            return []
        if not all(latest_status.get(worker_id) == "applied" for worker_id in expected_worker_ids):
            return []
        source_targets = _source_node_targets_from_state(events)
        if not source_targets:
            source_targets = _source_node_targets_from_runtime_config(self.config_store)
        return [ControlPlaneStartWorkEvent(source_targets=tuple(dict.fromkeys(source_targets)))]


@node(
    name="system.cp.start_work_command_dispatch",
    consumes=[ControlPlaneRootLeafStartWorkCommand],
    emits=[],
)
@dataclass
class ControlPlaneRootLeafStartWorkDispatchNode:
    control_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_CONTROL,
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafStartWorkCommand):
            return []
        event = ControlPlaneLeafStartWorkEvent(
            source_targets=payload.source_targets,
            command_id=payload.command_id,
        )
        self.control_lane_ipc.send(
            compose_execution_ipc_worker_target_id(
                payload.worker_id,
                lane=EXECUTION_IPC_LANE_CONTROL,
            ),
            event,
            no_reply=True,
        )
        return []


@dataclass(frozen=True, slots=True)
class _ControlPlaneGroupStartupReadyMarker:
    group_name: str


@dataclass(frozen=True, slots=True)
class _ControlPlaneGroupStartupFailedMarker:
    group_name: str


@node(
    name="system.cp.spawn_dispatch",
    consumes=[ControlPlaneSpawnRequestedEvent],
    emits=[LogMessage],
)
@dataclass
class ControlPlaneSpawnDispatchNode:
    lifecycle: object | None = None
    log_factory: object | None = None

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneSpawnRequestedEvent):
            return []
        lifecycle = self.lifecycle
        if callable(getattr(lifecycle, "on_spawn_requested", None)):
            lifecycle.on_spawn_requested(payload)
        factory = self.log_factory
        if hasattr(factory, "spawn_requested"):
            return [factory.spawn_requested(payload)]
        return []


@node(
    name="system.cp.group_startup_wait",
    consumes=[ControlPlaneSpawnRequestedEvent, ControlPlaneLeafConfigAckEvent],
    emits=[LogMessage],
)
@dataclass
class ControlPlaneGroupStartupWaitNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    log_factory: object | None = None

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneSpawnRequestedEvent):
            self.state.append_event(payload)
            return []
        if not isinstance(payload, ControlPlaneLeafConfigAckEvent):
            return []
        self.state.append_event(payload)
        latest_spawn = _latest_group_spawn_for_startup(self.state.events(), payload.target_group)
        if latest_spawn is None:
            return []
        worker_ids = tuple(
            f"{latest_spawn.group_name}#{index + 1}" for index in range(latest_spawn.workers)
        )
        if payload.worker_id not in worker_ids:
            return []

        latest_acks = _latest_group_acks_for_startup(
            self.state.events(), latest_spawn.group_name, worker_ids
        )
        factory = self.log_factory
        if any(ack.status == "rejected" for ack in latest_acks.values()):
            if _group_failed_already_reported_for_startup(self.state.events(), latest_spawn.group_name):
                return []
            self.state.append_event(
                _ControlPlaneGroupStartupFailedMarker(group_name=latest_spawn.group_name)
            )
            if hasattr(factory, "group_startup_failed"):
                failing = next(
                    (ack for ack in latest_acks.values() if ack.status == "rejected"),
                    payload,
                )
                error_message = (
                    failing.error
                    if isinstance(failing.error, str) and failing.error
                    else "leaf startup rejected"
                )
                return [
                    factory.group_startup_failed(
                        event=latest_spawn,
                        worker_ids=worker_ids,
                        error=RuntimeError(error_message),
                    )
                ]
            return []
        if not all(
            isinstance(latest_acks.get(worker_id), ControlPlaneLeafConfigAckEvent)
            and latest_acks[worker_id].status == "applied"
            for worker_id in worker_ids
        ):
            return []
        if _group_ready_already_reported_for_startup(self.state.events(), latest_spawn.group_name):
            return []
        self.state.append_event(
            _ControlPlaneGroupStartupReadyMarker(group_name=latest_spawn.group_name)
        )
        if hasattr(factory, "group_startup_ready"):
            return [factory.group_startup_ready(event=latest_spawn, worker_ids=worker_ids)]
        return []


@node(
    name="system.cp.log_dispatch",
    consumes=[LogMessage],
    emits=[LogDispatchEvent],
)
@dataclass
class ControlPlaneLogDispatchNode:
    console_dispatch: object | None = None

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, LogMessage):
            return []
        publish = getattr(self.console_dispatch, "publish", None)
        if callable(publish):
            try:
                publish(payload)
            except Exception:
                pass
        return [
            LogDispatchEvent(
                payload=payload,
                attributes={"origin_node": "system.cp.log_dispatch"},
            )
        ]


@node(
    name="system.cp.log_dispatch_sink",
    consumes=[LogDispatchEvent],
    emits=[],
)
@dataclass
class ControlPlaneLogDispatchSinkNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        _ = msg
        return []


@node(
    name="system.cp.root_payload_sink",
    consumes=[dict, list, tuple, str, int, float, bool, bytes],
    emits=[],
)
@dataclass
class ControlPlaneRootPayloadSinkNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        _ = msg
        return []


@node(
    name="system.cp.shutdown_expected_groups",
    consumes=[ControlPlaneLaunchPlanEvent],
    emits=[],
)
@dataclass
class ControlPlaneShutdownExpectedGroupsNode:
    shutdown_readiness: ControlPlaneShutdownReadinessService = inject.service(
        ControlPlaneShutdownReadinessService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLaunchPlanEvent):
            return []
        expected_groups = tuple(
            sorted(
                {
                    group.group_name
                    for group in payload.plan.groups
                    if isinstance(group.group_name, str)
                    and group.group_name
                    and not _is_readiness_exempt_group_name(group.group_name)
                    and isinstance(group.workers, int)
                    and group.workers > 0
                }
            )
        )
        if not expected_groups:
            return []
        self.shutdown_readiness.configure_expected_groups(expected_groups)
        return []


@node(
    name="system.cp.consumer_registry_group_prune",
    consumes=[ControlPlaneLaunchPlanEvent],
    emits=[ControlPlaneConsumerRegistryRemoveNodesEvent],
)
@dataclass
class ControlPlaneConsumerRegistryGroupPruneNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLaunchPlanEvent):
            return []
        node_names = _launch_plan_group_node_names(payload.plan)
        if not node_names:
            return []
        return [ControlPlaneConsumerRegistryRemoveNodesEvent(node_names=node_names)]


@node(
    name="system.cp.bootstrap_dispatch",
    consumes=[ControlPlaneInitEvent],
    emits=[ControlPlaneRootPulse],
)
@dataclass
class ControlPlaneRootBootstrapDispatchNode:
    runtime_bootstrap: object | None = inject.service(ControlPlaneRootRuntimeBootstrapService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneInitEvent):
            return []
        runtime = payload.runtime if isinstance(payload.runtime, dict) else {}
        process_role = runtime.get("__process_role")
        if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
            return []
        _prepare_root_runtime_on_init_event(
            payload=payload,
            runtime_bootstrap=self.runtime_bootstrap,
        )
        return [ControlPlaneRootPulse(runtime=runtime)]


@node(
    name="system.cp.root_bootstrap",
    consumes=[ControlPlaneRootPulse],
    emits=[ControlPlaneDiscoveryStartRequestedEvent],
)
@dataclass
class ControlPlaneRootBootstrapNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootPulse):
            return []
        return [ControlPlaneDiscoveryStartRequestedEvent(runtime=payload.runtime)]


@node(
    name="system.cp.root_config_stream",
    consumes=[ControlPlaneRootPulse],
    emits=[
        SystemRuntimeConfigRecord,
        ObservabilityConfigRecord,
        ExecutionGroupConfigRecord,
        NodeConfigRecord,
        ControlPlaneConfigStreamCompletedEvent,
    ],
)
@dataclass
class ControlPlaneRootConfigStreamNode:
    config_stream: ControlPlaneConfigStreamService = inject.service(ControlPlaneConfigStreamService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootPulse):
            return []
        return list(self.config_stream.stream(payload.runtime))


@node(
    name="system.cp.system_config_apply",
    consumes=[SystemRuntimeConfigRecord],
    emits=[ControlPlaneSystemConfigAppliedEvent],
)
@dataclass
class ControlPlaneSystemConfigApplyNode:
    applier: ControlPlaneSystemConfigApplyService = inject.service(ControlPlaneSystemConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, SystemRuntimeConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        return [ControlPlaneSystemConfigAppliedEvent(record_id=payload.record_id)]


@node(
    name="system.cp.observability_config_apply",
    consumes=[ObservabilityConfigRecord],
    emits=[ControlPlaneObservabilityConfigAppliedEvent],
)
@dataclass
class ControlPlaneObservabilityConfigApplyNode:
    applier: ControlPlaneObservabilityConfigApplyService = inject.service(ControlPlaneObservabilityConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ObservabilityConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        return [ControlPlaneObservabilityConfigAppliedEvent(record_id=payload.record_id)]


@node(
    name="system.cp.node_config_apply",
    consumes=[NodeConfigRecord],
    emits=[ControlPlaneNodeConfigAppliedEvent],
)
@dataclass
class ControlPlaneNodeConfigApplyNode:
    applier: ControlPlaneNodeConfigApplyService = inject.service(ControlPlaneNodeConfigApplyService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, NodeConfigRecord):
            return []
        applied = self.applier.apply(payload)
        if not applied:
            return []
        node_name = payload.payload.get("name")
        return [
            ControlPlaneNodeConfigAppliedEvent(
                record_id=payload.record_id,
                node_name=node_name if isinstance(node_name, str) and node_name else None,
            )
        ]


@node(
    name="system.cp.config_apply_barrier",
    consumes=[
        ControlPlaneConfigStreamCompletedEvent,
        ControlPlaneSystemConfigAppliedEvent,
        ControlPlaneObservabilityConfigAppliedEvent,
        ControlPlaneNodeConfigAppliedEvent,
    ],
    emits=[ControlPlaneConfigApplyCompletedEvent],
)
@dataclass
class ControlPlaneConfigApplyBarrierNode:
    tracker: ControlPlaneConfigApplyTrackerService = inject.service(ControlPlaneConfigApplyTrackerService)
    config_store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        completed_now = False
        if isinstance(payload, ControlPlaneConfigStreamCompletedEvent):
            expected = resolve_expected_config_apply_counts(self.config_store)
            completed_now = self.tracker.mark_stream_completed(
                runtime=payload.runtime,
                expected_counts=expected,
            )
        elif isinstance(payload, ControlPlaneSystemConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="system_runtime")
        elif isinstance(payload, ControlPlaneObservabilityConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="observability")
        elif isinstance(payload, ControlPlaneNodeConfigAppliedEvent):
            completed_now = self.tracker.mark_section_applied(section="node")
        if not completed_now:
            return []
        progress = self.tracker.progress()
        runtime = progress.runtime if isinstance(progress.runtime, dict) else {}
        return [
            ControlPlaneConfigApplyCompletedEvent(
                runtime=runtime,
                expected_counts=dict(progress.expected_counts),
                applied_counts=dict(progress.applied_counts),
            )
        ]


@node(
    name="system.cp.discovery_pump",
    consumes=[ControlPlaneDiscoveryStartRequestedEvent, ControlPlaneDiscoveryBatchRequestedEvent],
    emits=[
        ControlPlaneDiscoveryBatchRequestedEvent,
        ControlPlaneDiscoveryBatchReadyEvent,
        ControlPlaneDiscoverySourceCompletedEvent,
        ControlPlaneDiscoveryCompletedEvent,
    ],
)
@dataclass
class ControlPlaneDiscoveryPumpNode:
    stream: ControlPlaneDiscoveryStreamService = inject.service(ControlPlaneDiscoveryStreamService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoveryStartRequestedEvent):
            return _target_discovery_pump_requests(list(self.stream.start(payload)))
        if isinstance(payload, ControlPlaneDiscoveryBatchRequestedEvent):
            return _target_discovery_pump_requests(list(self.stream.request_batch(payload)))
        return []


def _target_discovery_pump_requests(events: list[object]) -> list[object]:
    # Discovery pump can schedule next batch requests back to itself.
    # Emit those requests with explicit target to avoid strict-router implicit self-loop rejection.
    routed: list[object] = []
    for event in events:
        if isinstance(event, ControlPlaneDiscoveryBatchRequestedEvent):
            routed.append(
                Envelope(
                    payload=event,
                    target="system.cp.discovery_pump",
                )
            )
            continue
        routed.append(event)
    return routed


@node(
    name="system.cp.discovery_apply",
    consumes=[ControlPlaneDiscoveryBatchReadyEvent],
    emits=[],
)
@dataclass
class ControlPlaneDiscoveryApplyNode:
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoveryBatchReadyEvent):
            for entity in payload.entities:
                if isinstance(entity, ControlPlaneDiscoveryEntityRecord):
                    self.discovery.append_item(entity)
            return []
        return []


@node(
    name="system.cp.discovery_materialize",
    consumes=[ControlPlaneDiscoveryBatchReadyEvent],
    emits=[],
)
@dataclass
class ControlPlaneDiscoveryMaterializeNode:
    materializer: ControlPlaneDiscoveryMaterializationService = inject.service(
        ControlPlaneDiscoveryMaterializationService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneDiscoveryBatchReadyEvent):
            return []
        records = tuple(
            item
            for item in payload.entities
            if isinstance(item, ControlPlaneDiscoveryEntityRecord)
        )
        if not records:
            return []
        self.materializer.materialize(records)
        return []


@node(
    name="system.cp.discovery_finalize",
    consumes=[ControlPlaneDiscoverySourceCompletedEvent],
    emits=[],
)
@dataclass
class ControlPlaneDiscoveryFinalizeNode:
    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if isinstance(payload, ControlPlaneDiscoverySourceCompletedEvent):
            return []
        return []


@node(
    name="system.cp.startup_barrier",
    consumes=[
        ControlPlaneDiscoveryCompletedEvent,
        ControlPlaneConfigApplyCompletedEvent,
        ControlPlaneConfigStreamCompletedEvent,
    ],
    emits=[ControlPlaneDagAssemblyRequestedEvent],
)
@dataclass
class ControlPlaneStartupBarrierNode:
    barrier: ControlPlaneStartupBarrierService = inject.service(ControlPlaneStartupBarrierService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        opened_now = False
        runtime: dict[str, object] | None = None
        if isinstance(payload, ControlPlaneDiscoveryCompletedEvent):
            runtime = payload.runtime
            opened_now = self.barrier.mark_discovery_completed(runtime=runtime)
        elif isinstance(payload, (ControlPlaneConfigApplyCompletedEvent, ControlPlaneConfigStreamCompletedEvent)):
            runtime = payload.runtime
            opened_now = self.barrier.mark_config_completed(runtime=runtime)
        if opened_now and isinstance(runtime, dict):
            return [ControlPlaneDagAssemblyRequestedEvent(runtime=runtime)]
        return []


@dataclass
class ControlPlaneRootLeafConfigAssignNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafHelloEvent):
            return []
        self.state.append_event(payload)
        events = self.state.events()
        group = find_group_spec_from_state(events, payload.target_group)
        if group is None:
            return []
        revision = next_config_revision(events, payload.worker_id)
        card = ControlPlaneLeafConfigCardEvent(
            target_group=group.group_name,
            worker_id=payload.worker_id,
            config_id=f"{payload.worker_id}:cfg:{revision}",
            run_id="run",
            scenario_id="scenario",
            group_name=group.group_name,
            nodes=tuple(group.nodes),
            runner_profile=payload.runner_profile,
            worker_slot=worker_slot_from_worker_id(payload.worker_id),
            config_revision=revision,
        )
        self.state.append_event(card)
        return [card]


@dataclass
class ControlPlaneRootLeafConfigAckNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafConfigAckEvent):
            return []
        self.state.append_event(payload)
        return []


@node(
    name="system.cp.root_stop_dispatch",
    consumes=[ControlPlaneRootLeafStopRequestEvent],
    emits=[],
)
@dataclass
class ControlPlaneRootLeafStopDispatchNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    control_lane_ipc: ExecutionIpcKvStreamPort = inject.kv_stream(
        ExecutionIpcKvStreamPort,
        qualifier=EXECUTION_IPC_LANE_CONTROL,
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafStopRequestEvent):
            return []
        self.state.append_event(payload)
        command = ControlPlaneLeafStopCommand(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            command_id=payload.command_id,
            reason=payload.reason,
        )
        self.state.append_event(command)
        self.control_lane_ipc.send(
            compose_execution_ipc_worker_target_id(
                payload.worker_id,
                lane=EXECUTION_IPC_LANE_CONTROL,
            ),
            command,
            no_reply=True,
        )
        return []


@dataclass
class ControlPlaneRootLeafStopAckNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafStopAckEvent):
            return []
        self.state.append_event(payload)
        return []


@dataclass
class ControlPlaneRootLeafBoundaryDispatchNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneRootLeafBoundaryExecuteRequestEvent):
            return []
        self.state.append_event(payload)
        command = ControlPlaneLeafBoundaryExecuteCommand(
            target_group=payload.target_group,
            worker_id=payload.worker_id,
            request_id=payload.request_id,
            inputs=tuple(payload.inputs),
            finalize=payload.finalize,
        )
        self.state.append_event(command)
        return [command]


@dataclass
class ControlPlaneRootLeafDrainReadyNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    shutdown_readiness: ControlPlaneShutdownReadinessService = inject.service(
        ControlPlaneShutdownReadinessService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        from stream_kernel.platform.services.runtime.control_plane_events import (
            ControlPlaneLeafDrainReadyEvent,
        )

        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneLeafDrainReadyEvent):
            return []
        emit_shutdown, snapshot = self.shutdown_readiness.mark_leaf_ready(payload)
        self.state.append_event(
            {
                "kind": "control_plane.shutdown.leaf_ready",
                "target_group": payload.target_group,
                "worker_id": payload.worker_id,
                "request_id": payload.request_id,
                "tombstone_output": payload.tombstone_output,
                "ready_groups": list(snapshot.ready_groups),
                "missing_groups": list(snapshot.missing_groups),
            }
        )
        if not emit_shutdown:
            return []
        event = ControlPlaneShutdownReadyEvent(
            expected_groups=snapshot.expected_groups,
            ready_groups=snapshot.ready_groups,
        )
        self.state.append_event(event)
        self.state.append_event(
            {
                "kind": "control_plane.shutdown.all_ready",
                "expected_groups": list(snapshot.expected_groups),
                "ready_groups": list(snapshot.ready_groups),
            }
        )
        return [event]


@node(
    name="system.cp.root_stop",
    consumes=[ControlPlaneShutdownReadyEvent],
    emits=[ControlPlaneRootLeafStopRequestEvent, PlatformSchedulerCancelCommand],
)
@dataclass
class ControlPlaneRootStopNode:
    state: ControlPlaneStateService = inject.service(ControlPlaneStateService)
    runner_control: ControlPlaneRootRunnerControlService = inject.service(
        ControlPlaneRootRunnerControlService
    )

    def __call__(self, msg: object, _ctx: object | None) -> list[object]:
        payload = msg.payload if isinstance(msg, Envelope) else msg
        if not isinstance(payload, ControlPlaneShutdownReadyEvent):
            return []
        events = self.state.events()
        requests: list[ControlPlaneRootLeafStopRequestEvent] = []
        seen: set[str] = set()
        for group_name, worker_id in _spawned_workers_for_shutdown(
            events=events,
            expected_groups=None,
        ):
            if worker_id in seen:
                continue
            seen.add(worker_id)
            requests.append(
                ControlPlaneRootLeafStopRequestEvent(
                    target_group=group_name,
                    worker_id=worker_id,
                    command_id=f"runtime-stop:{worker_id}",
                    reason="control_plane.shutdown_ready",
                )
            )
        cancel_commands = _root_leaf_ingress_scheduler_cancel_commands(
            expected_groups=None,
            events=events,
        )
        self.runner_control.request_stop()
        return [*requests, *cancel_commands]


def _root_leaf_ingress_scheduler_cancel_commands(
    *,
    expected_groups: tuple[str, ...] | None,
    events: list[object],
) -> list[PlatformSchedulerCancelCommand]:
    lanes = (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
    )
    commands: list[PlatformSchedulerCancelCommand] = []
    for _group_name, worker_id in _spawned_workers_for_shutdown(
        events=events,
        expected_groups=expected_groups,
    ):
        for lane in lanes:
            source_name = f"source:system.cp.root_leaf_ingress:{worker_id}:{lane}"
            commands.append(
                PlatformSchedulerCancelCommand(
                    job_id=f"cp.root.leaf_ingress:{source_name}",
                )
            )
    return commands


def resolve_group_specs(
    runtime: dict[str, object],
    *,
    config_store: ControlPlaneStartupConfigStore | object | None = None,
    discovered_node_names: set[str] | None = None,
) -> list[ControlPlaneGroupSpec]:
    from_store = _resolve_group_specs_from_store(
        config_store,
        discovered_node_names=discovered_node_names,
    )
    if from_store:
        return from_store
    if not isinstance(runtime, dict):
        return []
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if not isinstance(name, str) or not name:
            continue
        workers = group.get("workers", 1)
        if not isinstance(workers, int) or workers <= 0:
            continue
        raw_nodes = group.get("nodes", [])
        nodes = (
            tuple(node_name for node_name in raw_nodes if isinstance(node_name, str) and node_name)
            if isinstance(raw_nodes, list)
            else ()
        )
        nodes = _filter_group_nodes(nodes, discovered_node_names=discovered_node_names)
        resolved.append(ControlPlaneGroupSpec(group_name=name, workers=workers, nodes=nodes))
    return resolved


def _resolve_group_specs_from_store(
    config_store: ControlPlaneStartupConfigStore | object | None,
    *,
    discovered_node_names: set[str] | None = None,
) -> list[ControlPlaneGroupSpec]:
    store = _config_store_optional(config_store)
    if store is None:
        return []
    try:
        records = list(store.records(section="execution_group"))
    except Exception:
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for record in records:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            continue
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            continue
        workers = payload.get("workers", 1)
        if not isinstance(workers, int) or workers <= 0:
            continue
        raw_nodes = payload.get("nodes", [])
        nodes = (
            tuple(node_name for node_name in raw_nodes if isinstance(node_name, str) and node_name)
            if isinstance(raw_nodes, list)
            else ()
        )
        nodes = _filter_group_nodes(nodes, discovered_node_names=discovered_node_names)
        resolved.append(ControlPlaneGroupSpec(group_name=name, workers=workers, nodes=nodes))
    return resolved


def _config_store_optional(
    value: ControlPlaneStartupConfigStore | object | None,
) -> ControlPlaneStartupConfigStore | None:
    if isinstance(value, ControlPlaneStartupConfigStore):
        return value
    if callable(getattr(value, "records", None)) and callable(getattr(value, "all_records", None)):
        return value  # type: ignore[return-value]
    return None


def _discovered_node_names(value: ControlPlaneDiscoveryService | object | None) -> set[str] | None:
    if isinstance(value, ControlPlaneDiscoveryService):
        service = value
    elif callable(getattr(value, "items", None)):
        service = value  # type: ignore[assignment]
    else:
        return None
    items: list[object]
    try:
        records_method = getattr(service, "entity_records", None)
        if callable(records_method):
            items = list(records_method(kind="node"))
        else:
            items = list(service.items())  # type: ignore[call-arg]
    except Exception:
        return None
    names: set[str] = set()
    for item in items:
        if isinstance(item, ControlPlaneDiscoveryEntityRecord):
            name = item.meta.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    if not names:
        return None
    return names


def _filter_group_nodes(
    nodes: tuple[str, ...],
    *,
    discovered_node_names: set[str] | None,
) -> tuple[str, ...]:
    if not discovered_node_names:
        return nodes
    return tuple(name for name in nodes if name in discovered_node_names)


def find_group_spec_from_state(events: list[object], target_group: str) -> ControlPlaneGroupSpec | None:
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLaunchPlanEvent):
            continue
        for group in event.plan.groups:
            if group.group_name == target_group:
                return group
    return None


def next_config_revision(events: list[object], worker_id: str) -> int:
    count = 0
    for event in events:
        if isinstance(event, ControlPlaneLeafConfigCardEvent) and event.worker_id == worker_id:
            count += 1
    return count + 1


def worker_slot_from_worker_id(worker_id: str) -> int | None:
    if "#" not in worker_id:
        return None
    _, raw_index = worker_id.rsplit("#", 1)
    try:
        parsed = int(raw_index)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed - 1


def _launch_plan_signature(plan: ControlPlaneLaunchPlan) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    return tuple(
        (
            group.group_name,
            group.workers,
            tuple(group.nodes),
        )
        for group in plan.groups
    )


def _source_node_targets_from_state(events: list[object]) -> tuple[str, ...]:
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLaunchPlanEvent):
            continue
        targets: list[str] = []
        for group in event.plan.groups:
            for node_name in group.nodes:
                if isinstance(node_name, str) and node_name.startswith("source:"):
                    targets.append(node_name)
        if targets:
            return tuple(targets)
    return ()


@dataclass(frozen=True, slots=True)
class _StartWorkDispatchedMarker:
    worker_count: int


def _latest_launch_plan_from_state(events: list[object]) -> ControlPlaneLaunchPlan | None:
    for event in reversed(events):
        if isinstance(event, ControlPlaneLaunchPlanEvent):
            return event.plan
    return None


def _expected_worker_ids_for_start_work(events: list[object]) -> list[str]:
    plan = _latest_launch_plan_from_state(events)
    if isinstance(plan, ControlPlaneLaunchPlan):
        worker_ids: list[str] = []
        seen: set[str] = set()
        for group in plan.groups:
            if _is_readiness_exempt_group_name(group.group_name):
                continue
            for index in range(max(1, int(group.workers))):
                worker_id = f"{group.group_name}#{index + 1}"
                if worker_id in seen:
                    continue
                seen.add(worker_id)
                worker_ids.append(worker_id)
        return worker_ids

    # Fallback path when launch plan is unavailable in state.
    worker_ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        worker_id = event.worker_id if isinstance(event, ControlPlaneLeafConfigCardEvent) else None
        if (
            isinstance(worker_id, str)
            and worker_id
            and not _is_readiness_exempt_worker_id(worker_id)
            and worker_id not in seen
        ):
            seen.add(worker_id)
            worker_ids.append(worker_id)
    return worker_ids


def _latest_worker_statuses_for_start_work(
    *,
    events: list[object],
    expected_worker_ids: list[str],
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    expected = set(expected_worker_ids)
    for event in events:
        if not isinstance(event, ControlPlaneLeafConfigAckEvent):
            continue
        worker_id = event.worker_id
        if worker_id not in expected:
            continue
        statuses[worker_id] = event.status
    return statuses


def _spawned_workers_for_shutdown(
    *,
    events: list[object],
    expected_groups: tuple[str, ...] | None,
) -> list[tuple[str, str]]:
    expected = set(expected_groups) if isinstance(expected_groups, tuple) and expected_groups else None
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") != "control_plane.lifecycle.worker_spawned":
            continue
        group_name = event.get("group_name")
        worker_id = event.get("worker_id")
        if not isinstance(group_name, str) or not group_name:
            continue
        if not isinstance(worker_id, str) or not worker_id:
            continue
        if expected is not None and group_name not in expected:
            continue
        if worker_id in seen:
            continue
        seen.add(worker_id)
        pairs.append((group_name, worker_id))
    return pairs


def _is_start_work_on_all_ready_enabled(config_store: ControlPlaneStartupConfigStore) -> bool:
    records = list(config_store.records(section="system_runtime"))
    if not records:
        return True
    latest = records[-1]
    payload = latest.payload if isinstance(latest.payload, dict) else {}
    platform = payload.get("platform")
    if not isinstance(platform, dict):
        return True
    readiness = platform.get("readiness")
    if not isinstance(readiness, dict):
        return True
    enabled = readiness.get("enabled", True)
    if isinstance(enabled, bool) and not enabled:
        return False
    start_on_all_ready = readiness.get("start_work_on_all_groups_ready", True)
    return bool(start_on_all_ready) if isinstance(start_on_all_ready, bool) else True


def _source_node_targets_from_runtime_config(
    config_store: ControlPlaneStartupConfigStore,
) -> tuple[str, ...]:
    records = list(config_store.records(section="system_runtime"))
    if not records:
        return ()
    latest = records[-1]
    payload = latest.payload if isinstance(latest.payload, dict) else {}
    platform = payload.get("platform")
    if not isinstance(platform, dict):
        return ()
    raw_groups = platform.get("process_groups")
    if not isinstance(raw_groups, list):
        return ()
    targets: list[str] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        nodes = group.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node_name in nodes:
            if isinstance(node_name, str) and node_name.startswith("source:"):
                targets.append(node_name)
    return tuple(targets)


def _latest_group_spawn_for_startup(
    events: list[object],
    group_name: str,
) -> ControlPlaneSpawnRequestedEvent | None:
    for event in reversed(events):
        if isinstance(event, ControlPlaneSpawnRequestedEvent) and event.group_name == group_name:
            return event
    return None


def _latest_group_acks_for_startup(
    events: list[object],
    group_name: str,
    worker_ids: tuple[str, ...],
) -> dict[str, ControlPlaneLeafConfigAckEvent]:
    remaining = set(worker_ids)
    latest: dict[str, ControlPlaneLeafConfigAckEvent] = {}
    for event in reversed(events):
        if not isinstance(event, ControlPlaneLeafConfigAckEvent):
            continue
        if event.target_group != group_name:
            continue
        if event.worker_id not in remaining:
            continue
        latest[event.worker_id] = event
        remaining.remove(event.worker_id)
        if not remaining:
            break
    return latest


def _group_ready_already_reported_for_startup(
    events: list[object],
    group_name: str,
) -> bool:
    return any(
        isinstance(event, _ControlPlaneGroupStartupReadyMarker)
        and event.group_name == group_name
        for event in events
    )


def _group_failed_already_reported_for_startup(
    events: list[object],
    group_name: str,
) -> bool:
    return any(
        isinstance(event, _ControlPlaneGroupStartupFailedMarker)
        and event.group_name == group_name
        for event in events
    )


def _is_readiness_exempt_group_name(group_name: str) -> bool:
    lowered = group_name.strip().lower()
    return lowered.startswith("system.observability")


def _is_readiness_exempt_worker_id(worker_id: str) -> bool:
    lowered = worker_id.strip().lower()
    return lowered.startswith("system.observability#")


def _launch_plan_group_node_names(plan: ControlPlaneLaunchPlan) -> tuple[str, ...]:
    node_names: set[str] = set()
    for group in plan.groups:
        if not isinstance(group, ControlPlaneGroupSpec):
            continue
        for node_name in group.nodes:
            if isinstance(node_name, str) and node_name:
                node_names.add(node_name)
    return tuple(sorted(node_names))


def _prepare_root_runtime_on_init_event(
    *,
    payload: ControlPlaneInitEvent,
    runtime_bootstrap: object | None,
) -> None:
    prepare = getattr(runtime_bootstrap, "prepare_root_runtime", None)
    if not callable(prepare):
        return
    discovery = payload.discovery if isinstance(payload.discovery, dict) else None
    if not isinstance(discovery, dict):
        return
    raw = discovery.get("root_runtime_prepare")
    if not isinstance(raw, dict):
        return
    run_id = raw.get("run_id")
    scenario_id = raw.get("scenario_id")
    if not isinstance(run_id, str) or not run_id:
        return
    if not isinstance(scenario_id, str) or not scenario_id:
        return
    config = raw.get("config")
    adapters = raw.get("adapters")
    discovery_modules_raw = raw.get("discovery_modules")
    discovery_modules = (
        [item for item in discovery_modules_raw if isinstance(item, str) and item]
        if isinstance(discovery_modules_raw, (list, tuple))
        else []
    )
    prepare(
        runtime=payload.runtime if isinstance(payload.runtime, dict) else {},
        config=dict(config) if isinstance(config, dict) else {},
        adapters=dict(adapters) if isinstance(adapters, dict) else {},
        run_id=run_id,
        scenario_id=scenario_id,
        discovery_modules=discovery_modules,
    )


__all__ = [
    "ControlPlaneConfigApplyBarrierNode",
    "ControlPlaneDagAssemblyNode",
    "ControlPlaneDiscoveryApplyNode",
    "ControlPlaneDiscoveryMaterializeNode",
    "ControlPlaneDiscoveryFinalizeNode",
    "ControlPlaneDiscoveryPumpNode",
    "ControlPlaneGroupStartupWaitNode",
    "ControlPlaneInitPlanNode",
    "ControlPlaneLogDispatchNode",
    "ControlPlaneSpawnDispatchNode",
    "ControlPlaneStartWorkReadinessNode",
    "ControlPlaneStartWorkDispatchNode",
    "ControlPlaneRootLeafStartWorkDispatchNode",
    "ControlPlaneNodeConfigApplyNode",
    "ControlPlaneObservabilityConfigApplyNode",
    "ControlPlaneRootLeafBoundaryDispatchNode",
    "ControlPlaneRootConfigStreamNode",
    "ControlPlaneStartupBarrierNode",
    "ControlPlaneRootLeafConfigAckNode",
    "ControlPlaneRootLeafConfigAssignNode",
    "ControlPlaneRootLeafStopAckNode",
    "ControlPlaneRootLeafStopDispatchNode",
    "ControlPlaneRootLeafDrainReadyNode",
    "ControlPlaneRootBootstrapDispatchNode",
    "ControlPlaneRootBootstrapNode",
    "ControlPlaneConsumerRegistryGroupPruneNode",
    "ControlPlaneShutdownExpectedGroupsNode",
    "ControlPlaneRootStopNode",
    "ControlPlaneSystemConfigApplyNode",
]
