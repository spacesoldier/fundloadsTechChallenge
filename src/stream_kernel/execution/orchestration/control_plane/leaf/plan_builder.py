from __future__ import annotations

from collections.abc import Callable

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.execution.orchestration.control_plane.system_plan import (
    ControlPlaneSystemPlan,
)
from stream_kernel.execution.orchestration.control_plane.initialization_nodes import (
    ControlPlaneInitializationDispatchNode,
    ControlPlaneInitializationPlanNode,
    ControlPlaneNodeInitializeNode,
    ControlPlaneReadyForWorkNode,
)
from stream_kernel.execution.orchestration.scheduler_system_nodes import (
    SCHEDULER_COMMAND_NODE_NAME,
    SCHEDULER_TIMER_NODE_NAME,
    SCHEDULER_TICK_NODE_NAME,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryBindingsApplyEvent,
    ControlPlaneInitEvent,
    ControlPlaneDeferredMessageHoldEvent,
    ControlPlaneDeferredMessageReplayRequestEvent,
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneInitializationCompletedEvent,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryOutputsEvent,
    ControlPlaneLeafSinkDispatchAckEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafPulse,
    ControlPlaneNodeInitializeCommand,
    ControlPlaneReadyForWorkEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneLeafStopCommand,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.platform.services.runtime.control_plane_node_initialization import (
    ControlPlaneNodeInitializationService,
    InMemoryControlPlaneNodeInitializationService,
)

from ..consumer_registry_nodes import (
    ControlPlaneConsumerRegistryBindingsBootstrapNode,
    ControlPlaneConsumerRegistryBindingsApplyNode,
    ControlPlaneConsumerRegistryDiscoveryApplyNode,
    ControlPlaneConsumerRegistryRemoveNode,
    ControlPlaneDeferredMessageHoldNode,
    ControlPlaneDeferredMessageReplayNode,
)
from .static_graph import (
    LEAF_STATIC_SYSTEM_NODE_NAMES,
    build_leaf_static_system_steps,
)
from .system_nodes import (
    ControlPlaneLeafBootstrapDispatchNode,
    ControlPlaneLeafBootstrapNode,
    ControlPlaneLeafBoundaryExecuteNode,
    ControlPlaneLeafCommandIngressSourceNode,
    ControlPlaneLeafConfigApplyRuntimeNode,
    ControlPlaneLeafDiscoveryRequestNode,
    ControlPlaneLeafReplyDispatchNode,
    ControlPlaneLeafSourcePollFromSinkAckNode,
    ControlPlaneLeafSnapshotApplyNode,
    ControlPlaneLeafStartWorkNode,
    ControlPlaneLeafStopNode,
    ControlPlaneLeafTombstoneFinalizeNode,
    leaf_command_ingress_source_node_name,
    leaf_command_ingress_source_lanes,
    leaf_runtime_ingress_drain_source_node_name,
    leaf_runtime_ingress_source_lanes,
)


def build_leaf_control_plane_system_plan(
    *,
    runtime: dict[str, object],
    scenario_scope: ScenarioScope,
    resolve_required_service: Callable[..., object],
    resolve_optional_service: Callable[..., object | None],
    inject_control_plane_steps: Callable[[list[StepSpec], ScenarioScope], None],
    leaf_runtime_activation_contract: Callable[[], type[object]],
    leaf_boundary_execution_contract: Callable[[], type[object]],
    leaf_snapshot_apply_contract: Callable[[], type[object]],
    leaf_shutdown_readiness_contract: Callable[[], type[object]],
    noop_leaf_shutdown_readiness_service: Callable[[], object],
) -> ControlPlaneSystemPlan:
    bootstrap_dispatch = ControlPlaneLeafBootstrapDispatchNode()
    leaf = ControlPlaneLeafBootstrapNode()
    initialization_service = resolve_optional_service(
        scope=scenario_scope,
        contract=ControlPlaneNodeInitializationService,
        method_name="begin_phase",
    ) or InMemoryControlPlaneNodeInitializationService(store=InMemoryKvStore())
    initialization_dispatch = ControlPlaneInitializationDispatchNode()
    initialization_plan = ControlPlaneInitializationPlanNode(
        initialization=initialization_service,
    )
    node_initialize = ControlPlaneNodeInitializeNode(
        initialization=initialization_service,
    )
    ready_for_work = ControlPlaneReadyForWorkNode()
    consumer_registry_bindings_bootstrap = ControlPlaneConsumerRegistryBindingsBootstrapNode()
    observability_source_workers = _leaf_observability_source_worker_ids(runtime)
    command_ingress_lanes = leaf_command_ingress_source_lanes()
    runtime_ingress_lanes = _leaf_runtime_ingress_lanes_for_runtime(runtime)
    command_ingress_source_name = leaf_command_ingress_source_node_name(
        lane=command_ingress_lanes[0]
    )
    command_poll_worker_ids_by_lane = _leaf_poll_worker_ids_by_lane(
        lanes=command_ingress_lanes,
        observability_source_workers=(),
    )
    leaf_ingress_source_steps = [
        StepSpec(
            name=command_ingress_source_name,
            step=ControlPlaneLeafCommandIngressSourceNode(
                lane=command_ingress_lanes[0],
                poll_lanes=tuple(command_ingress_lanes),
                poll_worker_ids=(),
                poll_worker_ids_by_lane=command_poll_worker_ids_by_lane,
                source_name=command_ingress_source_name,
                max_messages_per_poll=64,
            ),
        )
    ]
    if runtime_ingress_lanes:
        runtime_ingress_source_name = leaf_runtime_ingress_drain_source_node_name()
        runtime_poll_worker_ids_by_lane = _leaf_poll_worker_ids_by_lane(
            lanes=runtime_ingress_lanes,
            observability_source_workers=observability_source_workers,
        )
        leaf_ingress_source_steps.append(
            StepSpec(
                name=runtime_ingress_source_name,
                step=ControlPlaneLeafCommandIngressSourceNode(
                    lane=runtime_ingress_lanes[0],
                    poll_lanes=tuple(runtime_ingress_lanes),
                    poll_worker_ids=(),
                    poll_worker_ids_by_lane=runtime_poll_worker_ids_by_lane,
                    source_name=runtime_ingress_source_name,
                    max_messages_per_poll=64,
                ),
            )
        )
    leaf_ingress_source_names = [spec.name for spec in leaf_ingress_source_steps]
    leaf_reply_dispatch = ControlPlaneLeafReplyDispatchNode()
    leaf_source_poll_from_sink_ack = ControlPlaneLeafSourcePollFromSinkAckNode()
    leaf_apply = ControlPlaneLeafConfigApplyRuntimeNode(
        activation=resolve_required_service(
            scope=scenario_scope,
            contract=leaf_runtime_activation_contract(),
            method_name="apply_config",
        )
    )
    leaf_discovery = ControlPlaneLeafDiscoveryRequestNode(
        discovery=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDiscoveryService,
            method_name="append_item",
        ),
    )
    leaf_snapshot = ControlPlaneLeafSnapshotApplyNode(
        snapshot_apply=resolve_required_service(
            scope=scenario_scope,
            contract=leaf_snapshot_apply_contract(),
            method_name="apply_snapshot",
        )
    )
    consumer_registry_bindings_apply = ControlPlaneConsumerRegistryBindingsApplyNode()
    deferred_message_hold = ControlPlaneDeferredMessageHoldNode()
    deferred_message_replay = ControlPlaneDeferredMessageReplayNode()
    consumer_registry_discovery_apply = ControlPlaneConsumerRegistryDiscoveryApplyNode()
    consumer_registry_remove = ControlPlaneConsumerRegistryRemoveNode()
    leaf_shutdown_readiness = resolve_optional_service(
        scope=scenario_scope,
        contract=leaf_shutdown_readiness_contract(),
        method_name="observe_boundary_outputs",
    ) or noop_leaf_shutdown_readiness_service()
    leaf_boundary = ControlPlaneLeafBoundaryExecuteNode(
        boundary_execution=resolve_required_service(
            scope=scenario_scope,
            contract=leaf_boundary_execution_contract(),
            method_name="execute",
        )
    )
    leaf_start_work = ControlPlaneLeafStartWorkNode()
    leaf_stop = ControlPlaneLeafStopNode()
    leaf_tombstone_finalize = ControlPlaneLeafTombstoneFinalizeNode(
        readiness=leaf_shutdown_readiness
    )
    leaf_steps = [
        *build_leaf_static_system_steps(
            bootstrap_dispatch=bootstrap_dispatch,
            leaf=leaf,
            initialization_dispatch=initialization_dispatch,
            initialization_plan=initialization_plan,
            node_initialize=node_initialize,
            ready_for_work=ready_for_work,
            consumer_registry_bindings_bootstrap=consumer_registry_bindings_bootstrap,
            consumer_registry_bindings_apply=consumer_registry_bindings_apply,
            deferred_message_hold=deferred_message_hold,
            deferred_message_replay=deferred_message_replay,
            leaf_reply_dispatch=leaf_reply_dispatch,
            leaf_source_poll_from_sink_ack=leaf_source_poll_from_sink_ack,
            consumer_registry_discovery_apply=consumer_registry_discovery_apply,
            consumer_registry_remove=consumer_registry_remove,
            leaf_discovery=leaf_discovery,
            leaf_snapshot=leaf_snapshot,
            leaf_apply=leaf_apply,
            leaf_start_work=leaf_start_work,
            leaf_boundary=leaf_boundary,
            leaf_tombstone_finalize=leaf_tombstone_finalize,
            leaf_stop=leaf_stop,
        ),
        *leaf_ingress_source_steps,
    ]
    initialization_plan.candidate_node_names = tuple(
        spec.name
        for spec in leaf_steps
        if callable(getattr(spec.step, "initialize", None))
    )
    node_initialize.node_lookup = {spec.name: spec.step for spec in leaf_steps}
    inject_control_plane_steps(leaf_steps, scenario_scope)
    return ControlPlaneSystemPlan(
        system_steps=leaf_steps,
        system_consumers={
            ControlPlaneInitEvent: [
                "system.cp.consumer_registry_bindings_bootstrap",
                "system.cp.initialization_dispatch",
            ],
            ControlPlaneInitializationRequestedEvent: ["system.cp.initialization_plan"],
            ControlPlaneNodeInitializeCommand: ["system.cp.node_initialize"],
            ControlPlaneInitializationCompletedEvent: ["system.cp.ready_for_work"],
            ControlPlaneReadyForWorkEvent: ["system.cp.bootstrap_dispatch"],
            ControlPlaneLeafPulse: ["system.cp.leaf_bootstrap"],
            PlatformSchedulerUpsertCommand: [SCHEDULER_COMMAND_NODE_NAME, SCHEDULER_TIMER_NODE_NAME],
            PlatformSchedulerCancelCommand: [SCHEDULER_COMMAND_NODE_NAME, SCHEDULER_TIMER_NODE_NAME],
            PlatformSchedulerTickEvent: [SCHEDULER_TICK_NODE_NAME],
            ControlPlaneConsumerRegistryBindingsApplyEvent: [
                "system.cp.consumer_registry_bindings_apply",
            ],
            ControlPlaneDeferredMessageHoldEvent: ["system.cp.deferred_message_hold"],
            ControlPlaneDeferredMessageReplayRequestEvent: ["system.cp.deferred_message_replay"],
            BootstrapControl: [
                *list(leaf_ingress_source_names),
            ],
            ControlPlaneLeafDiscoveryRequestEvent: [
                "system.cp.leaf_discovery",
            ],
            ControlPlaneLeafDiscoverySnapshotEvent: [
                "system.cp.leaf_snapshot_apply",
                "system.cp.consumer_registry_discovery_apply",
            ],
            ControlPlaneConsumerRegistryRemoveNodesEvent: [
                "system.cp.consumer_registry_remove",
            ],
            ControlPlaneLeafConfigCardEvent: [
                "system.cp.leaf_apply_config",
            ],
            ControlPlaneLeafStartWorkEvent: [
                "system.cp.leaf_start_work",
            ],
            ControlPlaneLeafBoundaryExecuteCommand: [
                "system.cp.leaf_boundary_execute",
            ],
            ControlPlaneLeafBoundaryOutputsEvent: [
                "system.cp.leaf_tombstone_finalize",
                "system.cp.leaf_reply_dispatch",
            ],
            ControlPlaneLeafSinkDispatchAckEvent: [
                "system.cp.leaf_source_poll_from_sink_ack",
                "system.cp.leaf_tombstone_finalize",
            ],
            ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"],
            ControlPlaneLeafHelloEvent: ["system.cp.leaf_reply_dispatch"],
            ControlPlaneLeafDiscoveryAckEvent: ["system.cp.leaf_reply_dispatch"],
            ControlPlaneLeafConfigAckEvent: ["system.cp.leaf_reply_dispatch"],
            ControlPlaneLeafDrainReadyEvent: ["system.cp.leaf_reply_dispatch"],
            ControlPlaneLeafStopAckEvent: ["system.cp.leaf_reply_dispatch"],
        },
        system_node_names={
            *LEAF_STATIC_SYSTEM_NODE_NAMES,
            *leaf_ingress_source_names,
        },
    )


def _leaf_runtime_ingress_lanes_for_runtime(runtime: dict[str, object]) -> tuple[str, ...]:
    role = runtime.get("__process_role")
    if role == "observability_worker":
        return _leaf_observability_ingress_lanes()
    return (leaf_runtime_ingress_source_lanes()[0],)


def _leaf_observability_ingress_lanes() -> tuple[str, ...]:
    from stream_kernel.execution.transport.ipc.ipc_transport import (
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
        EXECUTION_IPC_LANE_TRACE,
    )

    return (
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )


def _leaf_observability_source_worker_ids(runtime: dict[str, object]) -> tuple[str, ...]:
    if not isinstance(runtime, dict):
        return ()
    if runtime.get("__process_role") != "observability_worker":
        return ()
    platform = runtime.get("platform")
    if not isinstance(platform, dict):
        return ()
    raw_groups = platform.get("process_groups")
    if not isinstance(raw_groups, list):
        return ()
    observability_group = _leaf_observability_group_name(runtime)
    source_worker_ids: list[str] = []
    seen: set[str] = set()
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        if group_name == observability_group:
            continue
        workers = group.get("workers", 1)
        worker_count = int(workers) if isinstance(workers, int) and workers > 0 else 1
        for index in range(worker_count):
            worker_id = f"{group_name}#{index + 1}"
            if worker_id in seen:
                continue
            seen.add(worker_id)
            source_worker_ids.append(worker_id)
    return tuple(source_worker_ids)


def _leaf_poll_worker_ids_by_lane(
    *,
    lanes: tuple[str, ...],
    observability_source_workers: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    if not observability_source_workers:
        return mapping
    for lane in lanes:
        if lane in _leaf_observability_ingress_lanes():
            mapping[lane] = observability_source_workers
    return mapping


def _leaf_observability_group_name(runtime: dict[str, object]) -> str:
    observability = runtime.get("observability")
    if not isinstance(observability, dict):
        return "system.observability"
    service_cfg = observability.get("service_process")
    if not isinstance(service_cfg, dict):
        service_cfg = observability.get("service_worker")
    if not isinstance(service_cfg, dict):
        return "system.observability"
    group_name = service_cfg.get("group_name")
    if not isinstance(group_name, str) or not group_name:
        return "system.observability"
    return group_name


__all__ = ["build_leaf_control_plane_system_plan"]
