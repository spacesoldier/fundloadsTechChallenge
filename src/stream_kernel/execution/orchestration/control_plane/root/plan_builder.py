from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.execution.orchestration.control_plane.system_plan import (
    ControlPlaneSystemPlan,
)
from stream_kernel.execution.orchestration.control_plane.initialization_nodes import (
    ControlPlaneInitializationPlanNode,
    ControlPlaneNodeInitializeNode,
    ControlPlaneReadyForWorkNode,
)
from stream_kernel.execution.orchestration.scheduler_system_nodes import (
    SCHEDULER_COMMAND_NODE_NAME,
    SCHEDULER_TIMER_NODE_NAME,
    SCHEDULER_TICK_NODE_NAME,
)
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import LogDispatchEvent
from stream_kernel.execution.orchestration.control_plane.consumer_registry_nodes import (
    ControlPlaneConsumerRegistryBindingsBootstrapNode,
    ControlPlaneConsumerRegistryBindingsApplyNode,
    ControlPlaneConsumerRegistryDiscoveryApplyNode,
    ControlPlaneConsumerRegistryRemoveNode,
    ControlPlaneDeferredMessageHoldNode,
    ControlPlaneDeferredMessageReplayNode,
)
from stream_kernel.platform.services.runtime.control_plane_config_apply import (
    ControlPlaneConfigApplyTrackerService,
    ControlPlaneNodeConfigApplyService,
    ControlPlaneObservabilityConfigApplyService,
    ControlPlaneSystemConfigApplyService,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneConfigStreamService,
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_dag_assembly import (
    ControlPlaneDagAssemblyService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryBindingsApplyEvent,
    ControlPlaneDeferredMessageHoldEvent,
    ControlPlaneDeferredMessageReplayRequestEvent,
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneConfigApplyCompletedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDagAssembledEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneInitEvent,
    ControlPlaneInitializationCompletedEvent,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
    ControlPlaneLeafStopAckEvent,
    ControlPlaneNodeInitializeCommand,
    ControlPlaneNodeConfigAppliedEvent,
    ControlPlaneObservabilityConfigAppliedEvent,
    ControlPlaneRootLeafStartWorkCommand,
    ControlPlaneRootLeafStopRequestEvent,
    ControlPlaneRootPulse,
    ControlPlaneReadyForWorkEvent,
    ControlPlaneShutdownReadyEvent,
    ControlPlaneSpawnRequestedEvent,
    ControlPlaneStartWorkEvent,
    ControlPlaneSystemConfigAppliedEvent,
    ExecutionGroupConfigRecord,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.platform.services.runtime.control_plane_node_initialization import (
    ControlPlaneNodeInitializationService,
    InMemoryControlPlaneNodeInitializationService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerCancelCommand,
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)

from .leaf_ingress_nodes import (
    ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME,
    ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
    ROOT_LEAF_INGRESS_SOURCE_NODE_NAME,
    ControlPlaneRootLeafIngressEnvelopeEvent,
    ControlPlaneRootBoundaryHandoffSinkNode,
    ControlPlaneRootLeafControlDispatchSinkNode,
    ControlPlaneRootLeafIngressSourceNode,
)
from .static_graph import (
    ROOT_STATIC_SYSTEM_NODE_NAMES,
    build_root_static_system_steps,
)
from .system_nodes import (
    ControlPlaneRootBootstrapDispatchNode,
    ControlPlaneConfigApplyBarrierNode,
    ControlPlaneDagAssemblyNode,
    ControlPlaneDiscoveryApplyNode,
    ControlPlaneDiscoveryFinalizeNode,
    ControlPlaneDiscoveryMaterializeNode,
    ControlPlaneDiscoveryPumpNode,
    ControlPlaneGroupStartupWaitNode,
    ControlPlaneInitPlanNode,
    ControlPlaneLogDispatchNode,
    ControlPlaneLogDispatchSinkNode,
    ControlPlaneNodeConfigApplyNode,
    ControlPlaneObservabilityConfigApplyNode,
    ControlPlaneRootBootstrapNode,
    ControlPlaneRootConfigStreamNode,
    ControlPlaneConsumerRegistryGroupPruneNode,
    ControlPlaneRootLeafDrainReadyNode,
    ControlPlaneRootLeafEventLogBridgeNode,
    ControlPlaneRootLeafStartWorkDispatchNode,
    ControlPlaneRootLeafStopAckNode,
    ControlPlaneRootLeafStopDispatchNode,
    ControlPlaneRootPayloadSinkNode,
    ControlPlaneRootStopNode,
    ControlPlaneShutdownExpectedGroupsNode,
    ControlPlaneSpawnDispatchNode,
    ControlPlaneStartupBarrierNode,
    ControlPlaneStartWorkDispatchNode,
    ControlPlaneStartWorkReadinessNode,
    ControlPlaneSystemConfigApplyNode,
)


def build_root_control_plane_system_plan(
    *,
    runtime: dict[str, object],
    scenario_scope: ScenarioScope,
    resolve_required_service: Callable[..., object],
    resolve_optional_service: Callable[..., object | None],
    inject_control_plane_steps: Callable[[list[StepSpec], ScenarioScope], None],
    root_leaf_ingress_contract: Callable[[], type[object]],
    root_lifecycle_orchestration_contract: Callable[[], type[object]],
    root_lifecycle_log_factory_contract: Callable[[], type[object]],
    root_lifecycle_console_dispatch_contract: Callable[[], type[object]],
    shutdown_readiness_contract: Callable[[], type[object]],
    noop_shutdown_readiness_service: Callable[[], object],
) -> ControlPlaneSystemPlan:
    bootstrap_dispatch = ControlPlaneRootBootstrapDispatchNode()
    root_bootstrap = ControlPlaneRootBootstrapNode()
    consumer_registry_bindings_bootstrap = ControlPlaneConsumerRegistryBindingsBootstrapNode()
    consumer_registry_bindings_apply = ControlPlaneConsumerRegistryBindingsApplyNode()
    deferred_message_hold = ControlPlaneDeferredMessageHoldNode()
    deferred_message_replay = ControlPlaneDeferredMessageReplayNode()
    root_config_stream = ControlPlaneRootConfigStreamNode(
        config_stream=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneConfigStreamService,
            method_name="stream",
        )
    )
    discovery_pump = ControlPlaneDiscoveryPumpNode(
        stream=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDiscoveryStreamService,
            method_name="start",
        )
    )
    discovery_apply = ControlPlaneDiscoveryApplyNode(
        discovery=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDiscoveryService,
            method_name="append_item",
        )
    )
    consumer_registry_discovery_apply = ControlPlaneConsumerRegistryDiscoveryApplyNode()
    consumer_registry_remove = ControlPlaneConsumerRegistryRemoveNode()
    discovery_materialize = ControlPlaneDiscoveryMaterializeNode()
    discovery_finalize = ControlPlaneDiscoveryFinalizeNode()
    system_config_apply = ControlPlaneSystemConfigApplyNode(
        applier=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneSystemConfigApplyService,
            method_name="apply",
        )
    )
    observability_config_apply = ControlPlaneObservabilityConfigApplyNode(
        applier=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneObservabilityConfigApplyService,
            method_name="apply",
        )
    )
    node_config_apply = ControlPlaneNodeConfigApplyNode(
        applier=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneNodeConfigApplyService,
            method_name="apply",
        )
    )
    config_apply_barrier = ControlPlaneConfigApplyBarrierNode(
        tracker=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneConfigApplyTrackerService,
            method_name="mark_stream_completed",
        ),
        config_store=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStartupConfigStore,
            method_name="records",
        ),
    )
    startup_barrier = ControlPlaneStartupBarrierNode(
        barrier=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStartupBarrierService,
            method_name="mark_discovery_completed",
        )
    )
    dag_assembly = ControlPlaneDagAssemblyNode(
        assembly=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDagAssemblyService,
            method_name="assemble",
        ),
    )
    init_plan = ControlPlaneInitPlanNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
    )
    initialization_service = resolve_optional_service(
        scope=scenario_scope,
        contract=ControlPlaneNodeInitializationService,
        method_name="begin_phase",
    ) or InMemoryControlPlaneNodeInitializationService(store=InMemoryKvStore())
    initialization_plan = ControlPlaneInitializationPlanNode(
        initialization=initialization_service,
    )
    node_initialize = ControlPlaneNodeInitializeNode(
        initialization=initialization_service,
    )
    ready_for_work = ControlPlaneReadyForWorkNode()
    start_work_dispatch = ControlPlaneStartWorkDispatchNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
    )
    start_work_readiness = ControlPlaneStartWorkReadinessNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
        config_store=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStartupConfigStore,
            method_name="records",
        ),
    )
    start_work_command_dispatch = ControlPlaneRootLeafStartWorkDispatchNode()
    root_stop_dispatch = ControlPlaneRootLeafStopDispatchNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
    )
    root_leaf_ingress_sources = _build_root_leaf_ingress_source_steps(
        runtime=runtime,
        scope=scenario_scope,
        resolve_required_service=resolve_required_service,
        root_leaf_ingress_contract=root_leaf_ingress_contract,
    )
    root_leaf_ingress_source_names = [spec.name for spec in root_leaf_ingress_sources]
    root_reply_dispatch_sink = ControlPlaneRootLeafControlDispatchSinkNode()
    root_leaf_event_log_bridge = ControlPlaneRootLeafEventLogBridgeNode()
    root_log_dispatch_sink = ControlPlaneLogDispatchSinkNode()
    root_payload_sink = ControlPlaneRootPayloadSinkNode()
    root_boundary_handoff_sink = ControlPlaneRootBoundaryHandoffSinkNode(
        handoff=resolve_required_service(
            scope=scenario_scope,
            contract=ExecutionIpcHandoffDispatchService,
            method_name="dispatch_envelope",
        )
    )
    root_stop_ack_state = ControlPlaneRootLeafStopAckNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
    )
    shutdown_expected_groups = ControlPlaneShutdownExpectedGroupsNode(
        shutdown_readiness=resolve_optional_service(
            scope=scenario_scope,
            contract=shutdown_readiness_contract(),
            method_name="configure_expected_groups",
        ) or noop_shutdown_readiness_service(),
    )
    consumer_registry_group_prune = ControlPlaneConsumerRegistryGroupPruneNode()
    leaf_drain_ready = ControlPlaneRootLeafDrainReadyNode(
        state=resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
        shutdown_readiness=resolve_optional_service(
            scope=scenario_scope,
            contract=shutdown_readiness_contract(),
            method_name="mark_leaf_ready",
        ) or noop_shutdown_readiness_service(),
    )
    root_stop = ControlPlaneRootStopNode()
    lifecycle_steps: list[StepSpec] = []
    lifecycle_consumers: dict[type[Any], list[str]] = {}
    lifecycle_node_names: set[str] = set()
    lifecycle_orchestration = resolve_optional_service(
        scope=scenario_scope,
        contract=root_lifecycle_orchestration_contract(),
        method_name="on_spawn_requested",
    )
    lifecycle_log_factory = resolve_optional_service(
        scope=scenario_scope,
        contract=root_lifecycle_log_factory_contract(),
        method_name="spawn_requested",
    )
    lifecycle_console_dispatch = resolve_optional_service(
        scope=scenario_scope,
        contract=root_lifecycle_console_dispatch_contract(),
        method_name="publish",
    )
    root_log_dispatch = ControlPlaneLogDispatchNode(
        console_dispatch=lifecycle_console_dispatch,
    )
    if (
        lifecycle_orchestration is not None
        and lifecycle_log_factory is not None
    ):
        lifecycle_spawn_dispatch = ControlPlaneSpawnDispatchNode(
            lifecycle=lifecycle_orchestration,
            log_factory=lifecycle_log_factory,
        )
        lifecycle_group_startup_wait = ControlPlaneGroupStartupWaitNode(
            state=resolve_required_service(
                scope=scenario_scope,
                contract=ControlPlaneStateService,
                method_name="append_event",
            ),
            log_factory=lifecycle_log_factory,
        )
        lifecycle_steps = [
            StepSpec(name="system.cp.spawn_dispatch", step=lifecycle_spawn_dispatch),
            StepSpec(name="system.cp.group_startup_wait", step=lifecycle_group_startup_wait),
        ]
        lifecycle_consumers = {
            ControlPlaneSpawnRequestedEvent: [
                "system.cp.spawn_dispatch",
                "system.cp.group_startup_wait",
            ],
            ControlPlaneLeafConfigAckEvent: ["system.cp.group_startup_wait"],
        }
        lifecycle_node_names = {
            "system.cp.spawn_dispatch",
            "system.cp.group_startup_wait",
        }
    root_steps = [
        *build_root_static_system_steps(
            bootstrap_dispatch=bootstrap_dispatch,
            root_bootstrap=root_bootstrap,
            consumer_registry_bindings_bootstrap=consumer_registry_bindings_bootstrap,
            consumer_registry_bindings_apply=consumer_registry_bindings_apply,
            deferred_message_hold=deferred_message_hold,
            deferred_message_replay=deferred_message_replay,
            root_config_stream=root_config_stream,
            root_reply_dispatch_sink=root_reply_dispatch_sink,
            root_stop_ack_state=root_stop_ack_state,
            root_boundary_handoff_sink=root_boundary_handoff_sink,
            consumer_registry_discovery_apply=consumer_registry_discovery_apply,
            consumer_registry_remove=consumer_registry_remove,
            discovery_pump=discovery_pump,
            discovery_apply=discovery_apply,
            discovery_materialize=discovery_materialize,
            discovery_finalize=discovery_finalize,
            system_config_apply=system_config_apply,
            observability_config_apply=observability_config_apply,
            node_config_apply=node_config_apply,
            config_apply_barrier=config_apply_barrier,
            startup_barrier=startup_barrier,
            dag_assembly=dag_assembly,
            init_plan=init_plan,
            initialization_plan=initialization_plan,
            node_initialize=node_initialize,
            ready_for_work=ready_for_work,
            consumer_registry_group_prune=consumer_registry_group_prune,
            shutdown_expected_groups=shutdown_expected_groups,
            start_work_readiness=start_work_readiness,
            start_work_dispatch=start_work_dispatch,
            start_work_command_dispatch=start_work_command_dispatch,
            root_stop_dispatch=root_stop_dispatch,
            root_leaf_event_log_bridge=root_leaf_event_log_bridge,
            root_log_dispatch=root_log_dispatch,
            root_log_dispatch_sink=root_log_dispatch_sink,
            root_payload_sink=root_payload_sink,
            leaf_drain_ready=leaf_drain_ready,
            root_stop=root_stop,
        ),
        *root_leaf_ingress_sources,
        *lifecycle_steps,
    ]
    initialization_plan.candidate_node_names = tuple(
        spec.name
        for spec in root_steps
        if callable(getattr(spec.step, "initialize", None))
    )
    node_initialize.node_lookup = {spec.name: spec.step for spec in root_steps}
    inject_control_plane_steps(root_steps, scenario_scope)
    system_consumers: dict[type[Any], list[str]] = {
        ControlPlaneInitEvent: [
            "system.cp.consumer_registry_bindings_bootstrap",
            "system.cp.bootstrap_dispatch",
        ],
        ControlPlaneRootPulse: ["system.cp.root_bootstrap", "system.cp.root_config_stream"],
        PlatformSchedulerUpsertCommand: [SCHEDULER_COMMAND_NODE_NAME, SCHEDULER_TIMER_NODE_NAME],
        PlatformSchedulerCancelCommand: [SCHEDULER_COMMAND_NODE_NAME, SCHEDULER_TIMER_NODE_NAME],
        PlatformSchedulerTickEvent: [SCHEDULER_TICK_NODE_NAME, "system.cp.shutdown_leaf_ready"],
        ControlPlaneConsumerRegistryBindingsApplyEvent: [
            "system.cp.consumer_registry_bindings_apply",
        ],
        ControlPlaneDeferredMessageHoldEvent: [
            "system.cp.deferred_message_hold",
        ],
        ControlPlaneDeferredMessageReplayRequestEvent: [
            "system.cp.deferred_message_replay",
        ],
        BootstrapControl: root_leaf_ingress_source_names,
        ControlPlaneLeafHelloEvent: [
            ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneLeafDiscoveryAckEvent: [
            ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneLeafReplyDispatchDiagEvent: [
            ROOT_LEAF_CONTROL_DISPATCH_SINK_NODE_NAME,
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneLeafConfigAckEvent: [
            "system.cp.start_work_readiness",
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneLeafStopAckEvent: [
            "system.cp.root_stop_ack_state",
            "system.cp.root_stop",
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneStartWorkEvent: ["system.cp.start_work_dispatch"],
        ControlPlaneRootLeafStartWorkCommand: ["system.cp.start_work_command_dispatch"],
        ControlPlaneRootLeafStopRequestEvent: ["system.cp.root_stop_dispatch"],
        ControlPlaneDiscoveryStartRequestedEvent: ["system.cp.discovery_pump"],
        ControlPlaneDiscoveryBatchRequestedEvent: ["system.cp.discovery_pump"],
            ControlPlaneDiscoveryBatchReadyEvent: [
                "system.cp.discovery_apply",
                "system.cp.consumer_registry_discovery_apply",
                "system.cp.discovery_materialize",
            ],
            ControlPlaneConsumerRegistryRemoveNodesEvent: [
                "system.cp.consumer_registry_remove",
            ],
        ControlPlaneDiscoverySourceCompletedEvent: ["system.cp.discovery_finalize"],
        SystemRuntimeConfigRecord: ["system.cp.system_config_apply"],
        ObservabilityConfigRecord: ["system.cp.observability_config_apply"],
        NodeConfigRecord: ["system.cp.node_config_apply"],
        ControlPlaneConfigStreamCompletedEvent: ["system.cp.config_apply_barrier"],
        ControlPlaneSystemConfigAppliedEvent: ["system.cp.config_apply_barrier"],
        ControlPlaneObservabilityConfigAppliedEvent: ["system.cp.config_apply_barrier"],
        ControlPlaneNodeConfigAppliedEvent: ["system.cp.config_apply_barrier"],
        ControlPlaneDiscoveryCompletedEvent: ["system.cp.startup_barrier"],
        ControlPlaneConfigApplyCompletedEvent: ["system.cp.startup_barrier"],
        ControlPlaneDagAssemblyRequestedEvent: ["system.cp.dag_assembly"],
        ControlPlaneDagAssembledEvent: ["system.cp.init_plan"],
        ControlPlaneInitializationRequestedEvent: ["system.cp.initialization_plan"],
        ControlPlaneInitializationCompletedEvent: ["system.cp.ready_for_work"],
        ControlPlaneReadyForWorkEvent: ["system.cp.root_payload_sink"],
        ControlPlaneNodeInitializeCommand: ["system.cp.node_initialize"],
        ControlPlaneLaunchPlanEvent: [
            "system.cp.root_payload_sink",
            "system.cp.consumer_registry_group_prune",
        ],
        ControlPlaneSpawnRequestedEvent: ["system.cp.root_payload_sink"],
        LogMessage: ["system.cp.log_dispatch"],
        LogDispatchEvent: ["system.cp.log_dispatch_sink"],
        ExecutionGroupConfigRecord: ["system.cp.root_payload_sink"],
        dict: ["system.cp.root_payload_sink"],
        list: ["system.cp.root_payload_sink"],
        tuple: ["system.cp.root_payload_sink"],
        str: ["system.cp.root_payload_sink"],
        int: ["system.cp.root_payload_sink"],
        float: ["system.cp.root_payload_sink"],
        bool: ["system.cp.root_payload_sink"],
        bytes: ["system.cp.root_payload_sink"],
        ControlPlaneLeafDrainReadyEvent: [
            "system.cp.shutdown_leaf_ready",
            "system.cp.root_leaf_event_log_bridge",
        ],
        ControlPlaneShutdownReadyEvent: ["system.cp.root_stop"],
        ControlPlaneRootLeafIngressEnvelopeEvent: [ROOT_BOUNDARY_HANDOFF_SINK_NODE_NAME],
    }
    launch_plan_consumers = system_consumers.setdefault(ControlPlaneLaunchPlanEvent, [])
    if "system.cp.shutdown_expected_groups" not in launch_plan_consumers:
        launch_plan_consumers.append("system.cp.shutdown_expected_groups")
    for token, node_names in lifecycle_consumers.items():
        existing = system_consumers.setdefault(token, [])
        existing.extend(name for name in node_names if name not in existing)
    return ControlPlaneSystemPlan(
        system_steps=root_steps,
        system_consumers=system_consumers,
        system_node_names={
            *ROOT_STATIC_SYSTEM_NODE_NAMES,
            *root_leaf_ingress_source_names,
            *lifecycle_node_names,
        },
    )


def _build_root_leaf_ingress_source_steps(
    *,
    runtime: dict[str, object],
    scope: ScenarioScope,
    resolve_required_service: Callable[..., object],
    root_leaf_ingress_contract: Callable[[], type[object]],
) -> list[StepSpec]:
    ingress = resolve_required_service(
        scope=scope,
        contract=root_leaf_ingress_contract(),
        method_name="poll_next_leaf_ingress_for_worker_lane",
    )
    ingress_specs = _root_leaf_ingress_specs(runtime)
    if not ingress_specs:
        return []
    source_name = ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
    return [
        StepSpec(
            name=source_name,
            step=ControlPlaneRootLeafIngressSourceNode(
                ingress=ingress,
                source_name=source_name,
                ingress_specs=tuple(ingress_specs),
            ),
        )
    ]


def _root_leaf_ingress_specs(runtime: dict[str, object]) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for worker_id in _root_leaf_ingress_worker_ids(runtime):
        lanes = _root_leaf_ingress_lanes_for_worker(runtime=runtime, worker_id=worker_id)
        for lane in lanes:
            if not isinstance(lane, str) or not lane:
                continue
            specs.append((worker_id, lane))
    return specs


def _root_leaf_ingress_lanes() -> tuple[str, ...]:
    from stream_kernel.execution.transport.ipc.ipc_transport import (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
    )

    return (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
    )


def _root_leaf_ingress_lanes_for_worker(*, runtime: dict[str, object], worker_id: str) -> tuple[str, ...]:
    lanes = _root_leaf_ingress_lanes()
    if _resolve_data_plane_topology(runtime) != "ring":
        return lanes
    # Ring mode keeps root off the business data path for regular workers.
    # Only observability worker keeps data lane ingress to relay selected logs
    # back to root console path.
    if _is_observability_worker_id(runtime=runtime, worker_id=worker_id):
        return lanes
    return (lanes[0],)


def _resolve_data_plane_topology(runtime: dict[str, object]) -> str:
    if not isinstance(runtime, dict):
        return "star"
    platform = runtime.get("platform")
    if not isinstance(platform, dict):
        return "star"
    execution_ipc = platform.get("execution_ipc")
    if not isinstance(execution_ipc, dict):
        return "star"
    raw = execution_ipc.get("data_plane_topology", "star")
    if not isinstance(raw, str):
        return "star"
    normalized = raw.strip().lower()
    if normalized not in {"star", "ring"}:
        return "star"
    return normalized


def _is_observability_worker_id(*, runtime: dict[str, object], worker_id: str) -> bool:
    if not isinstance(worker_id, str) or not worker_id:
        return False
    group_name = _resolve_observability_group_name(runtime)
    if not isinstance(group_name, str) or not group_name:
        return False
    return worker_id.startswith(f"{group_name}#")


def _resolve_observability_group_name(runtime: dict[str, object]) -> str:
    if not isinstance(runtime, dict):
        return "system.observability"
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


def _root_leaf_ingress_worker_ids(runtime: dict[str, object]) -> tuple[str, ...]:
    if not isinstance(runtime, dict):
        return ()
    platform = runtime.get("platform")
    if not isinstance(platform, dict):
        return ()
    raw_groups = platform.get("process_groups")
    worker_ids: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            group_name = group.get("name")
            if not isinstance(group_name, str) or not group_name:
                continue
            raw_workers = group.get("workers", 1)
            worker_count = raw_workers if isinstance(raw_workers, int) and raw_workers > 0 else 1
            for index in range(worker_count):
                worker_id = f"{group_name}#{index + 1}"
                if worker_id in seen:
                    continue
                seen.add(worker_id)
                worker_ids.append(worker_id)

    observability = runtime.get("observability")
    if isinstance(observability, dict):
        service_cfg = observability.get("service_process")
        if not isinstance(service_cfg, dict):
            service_cfg = observability.get("service_worker")
        if isinstance(service_cfg, dict) and service_cfg.get("enabled") is True:
            group_name = service_cfg.get("group_name")
            if not isinstance(group_name, str) or not group_name:
                group_name = "system.observability"
            worker_id = f"{group_name}#1"
            if worker_id not in seen:
                seen.add(worker_id)
                worker_ids.append(worker_id)

    return tuple(worker_ids)


__all__ = ["build_root_control_plane_system_plan"]
