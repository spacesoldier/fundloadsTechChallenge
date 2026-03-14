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
    leaf_command_ingress_source_lanes,
    leaf_command_ingress_source_node_name,
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
    leaf_command_lanes = _leaf_command_source_lanes_for_runtime(runtime)
    leaf_command_source_steps = [
        StepSpec(
            name=source_name,
            step=ControlPlaneLeafCommandIngressSourceNode(
                lane=lane,
                source_name=source_name,
            ),
        )
        for lane in leaf_command_lanes
        for source_name in [leaf_command_ingress_source_node_name(lane=lane)]
    ]
    leaf_command_source_names = [spec.name for spec in leaf_command_source_steps]
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
        *leaf_command_source_steps,
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
            BootstrapControl: list(leaf_command_source_names),
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
            *leaf_command_source_names,
        },
    )


def _leaf_command_source_lanes_for_runtime(runtime: dict[str, object]) -> tuple[str, ...]:
    base = tuple(leaf_command_ingress_source_lanes())
    role = runtime.get("__process_role")
    if role != "observability_worker":
        return base
    from stream_kernel.execution.transport.ipc.ipc_transport import (
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
        EXECUTION_IPC_LANE_TRACE,
    )

    return (
        *base,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )


__all__ = ["build_leaf_control_plane_system_plan"]
