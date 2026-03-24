from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.control_plane.planning import (
    LEAF_STATIC_SYSTEM_NODE_NAMES,
    ROOT_STATIC_SYSTEM_NODE_NAMES,
    build_control_plane_system_plan,
)
from stream_kernel.execution.orchestration.control_plane.leaf.system_nodes import (
    leaf_runtime_ingress_drain_source_node_name,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_nodes import (
    ROOT_LEAF_INGRESS_SOURCE_NODE_NAME,
    ControlPlaneRootLeafIngressSourceNode,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
    ControlPlaneRootLeafIngressService,
)
from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
    ExecutionIpcHandoffDispatchService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
    LeafBoundaryExecutionService,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
    LeafRuntimeActivationService,
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
from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
    ControlPlaneLeafDiscoverySnapshotApplyService,
)
from stream_kernel.platform.services.runtime.control_plane_consumer_registry import (
    ControlPlaneDynamicConsumerRoutingService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerRegistryBindingsApplyEvent,
    ControlPlaneInitEvent,
    ControlPlaneDeferredMessageHoldEvent,
    ControlPlaneDeferredMessageReplayRequestEvent,
    ControlPlaneConsumerRegistryRemoveNodesEvent,
    ControlPlaneDagAssembledEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafPulse,
    ControlPlaneLeafStopCommand,
    ControlPlaneRootPulse,
    ControlPlaneShutdownReadyEvent,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)
from stream_kernel.platform.services.runtime.platform_scheduler import (
    PlatformSchedulerTickEvent,
    PlatformSchedulerUpsertCommand,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
)


class _ConfigStream:
    def stream(self, *, runtime):
        _ = runtime
        return []


class _DiscoveryStream:
    def start(self, *, runtime):
        _ = runtime
        return []


class _Discovery:
    def append_item(self, item):
        _ = item


class _Apply:
    def apply(self, record):
        _ = record
        return None


class _Tracker:
    def mark_stream_completed(self):
        return None


class _ConfigStore:
    def records(self, *, section=None):
        _ = section
        return []


class _Barrier:
    def mark_discovery_completed(self):
        return None


class _DagAssembly:
    def assemble(self, *, runtime, config_stream, discovery_records):
        _ = (runtime, config_stream, discovery_records)
        return None


class _State:
    def append_event(self, event):
        _ = event


class _LeafIngress(ControlPlaneRootLeafIngressService):
    def poll_next_leaf_ingress_for_worker_lane(
        self,
        *,
        worker_id: str,
        lane: str,
        timeout_seconds: float = 0.0,
    ):
        _ = (worker_id, lane, timeout_seconds)
        return None


class _LeafActivation:
    def apply_config(self, *, session, card):
        _ = (session, card)
        return None


class _LeafBoundaryExecution:
    def execute(self, *, session, inputs):
        _ = (session, inputs)
        return []


class _LeafSnapshotApply:
    def apply_snapshot(self, *, session, snapshot):
        _ = (session, snapshot)
        return None


class _DynamicConsumerRouting:
    def apply_bindings(self, bindings):
        _ = bindings

    def apply_discovery_records(self, records):
        _ = records

    def remove_node_bindings(self, node_names):
        _ = node_names


class _HandoffDispatch:
    def dispatch_envelope(self, envelope, *, source_group=None):
        _ = (envelope, source_group)
        return True


def test_control_plane_system_plan_includes_root_config_stream_node() -> None:
    registry = InjectionRegistry()
    registry.register_factory("service", ControlPlaneConfigStreamService, lambda: _ConfigStream())
    registry.register_factory("service", ControlPlaneDiscoveryStreamService, lambda: _DiscoveryStream())
    registry.register_factory("service", ControlPlaneDiscoveryService, lambda: _Discovery())
    registry.register_factory("service", ControlPlaneSystemConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneObservabilityConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneNodeConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneConfigApplyTrackerService, lambda: _Tracker())
    registry.register_factory("service", ControlPlaneStartupConfigStore, lambda: _ConfigStore())
    registry.register_factory("service", ControlPlaneStartupBarrierService, lambda: _Barrier())
    registry.register_factory("service", ControlPlaneDagAssemblyService, lambda: _DagAssembly())
    registry.register_factory("service", ControlPlaneStateService, lambda: _State())
    registry.register_factory(
        "service",
        ControlPlaneDynamicConsumerRoutingService,
        lambda: _DynamicConsumerRouting(),
    )
    registry.register_factory("service", ControlPlaneRootLeafIngressService, lambda: _LeafIngress())
    registry.register_factory("service", ExecutionIpcHandoffDispatchService, lambda: _HandoffDispatch())
    scope = registry.instantiate_for_scenario("s1")
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
        }
    }

    plan = build_control_plane_system_plan(runtime=runtime, scenario_scope=scope)
    step_names = {step.name for step in plan.system_steps}
    assert set(ROOT_STATIC_SYSTEM_NODE_NAMES).issubset(step_names)

    assert "system.cp.root_config_stream" in step_names
    assert "system.cp.discovery_pump" in step_names
    assert "system.cp.discovery_apply" in step_names
    assert "system.cp.discovery_materialize" in step_names
    assert "system.cp.discovery_finalize" in step_names
    assert "system.cp.dag_assembly" in step_names
    consumers = plan.system_consumers.get(ControlPlaneRootPulse, [])
    assert "system.cp.root_config_stream" in consumers
    assert "system.cp.root_bootstrap" in consumers
    assert "system.cp.discovery_pump" in plan.system_consumers.get(
        ControlPlaneDiscoveryStartRequestedEvent, []
    )
    assert "system.cp.discovery_pump" in plan.system_consumers.get(
        ControlPlaneDiscoveryBatchRequestedEvent, []
    )
    assert "system.cp.discovery_apply" in plan.system_consumers.get(
        ControlPlaneDiscoveryBatchReadyEvent, []
    )
    assert "system.cp.consumer_registry_discovery_apply" in plan.system_consumers.get(
        ControlPlaneDiscoveryBatchReadyEvent, []
    )
    assert "system.cp.discovery_materialize" in plan.system_consumers.get(
        ControlPlaneDiscoveryBatchReadyEvent, []
    )
    assert "system.cp.consumer_registry_bindings_apply" in plan.system_consumers.get(
        ControlPlaneConsumerRegistryBindingsApplyEvent,
        [],
    )
    assert "system.cp.consumer_registry_bindings_bootstrap" in step_names
    init_consumers = plan.system_consumers.get(ControlPlaneInitEvent, [])
    assert "system.cp.consumer_registry_bindings_bootstrap" in init_consumers
    assert "system.cp.bootstrap_dispatch" in init_consumers
    assert "system.cp.deferred_message_hold" in step_names
    assert "system.cp.deferred_message_replay" in step_names
    assert "system.cp.deferred_message_hold" in plan.system_consumers.get(
        ControlPlaneDeferredMessageHoldEvent,
        [],
    )
    assert "system.cp.deferred_message_replay" in plan.system_consumers.get(
        ControlPlaneDeferredMessageReplayRequestEvent,
        [],
    )
    assert "system.cp.consumer_registry_remove" in plan.system_consumers.get(
        ControlPlaneConsumerRegistryRemoveNodesEvent,
        [],
    )
    assert "system.cp.consumer_registry_bindings_apply" in plan.system_consumers.get(
        ControlPlaneConsumerRegistryBindingsApplyEvent,
        [],
    )
    assert "system.cp.discovery_finalize" in plan.system_consumers.get(
        ControlPlaneDiscoverySourceCompletedEvent, []
    )
    assert "system.cp.startup_barrier" in step_names
    assert "system.cp.shutdown_expected_groups" in step_names
    assert "system.cp.root_stop" in step_names
    assert "system.cp.root_leaf_event_log_bridge" in step_names
    assert "system.cp.log_dispatch" in step_names
    assert "system.cp.startup_barrier" in plan.system_consumers.get(
        ControlPlaneDiscoveryCompletedEvent, []
    )
    assert "system.cp.config_apply_barrier" in plan.system_consumers.get(
        ControlPlaneConfigStreamCompletedEvent, []
    )
    assert "system.cp.init_plan" in plan.system_consumers.get(
        ControlPlaneDagAssembledEvent, []
    )
    assert "system.cp.shutdown_expected_groups" in plan.system_consumers.get(
        ControlPlaneLaunchPlanEvent, []
    )
    assert "system.cp.consumer_registry_group_prune" in plan.system_consumers.get(
        ControlPlaneLaunchPlanEvent, []
    )
    assert "system.cp.root_stop" in plan.system_consumers.get(
        ControlPlaneShutdownReadyEvent, []
    )
    assert "system.cp.root_leaf_event_log_bridge" in plan.system_consumers.get(
        ControlPlaneLeafHelloEvent, []
    )
    assert "system.cp.root_leaf_event_log_bridge" in plan.system_consumers.get(
        ControlPlaneLeafReplyDispatchDiagEvent, []
    )
    assert "system.cp.log_dispatch" in plan.system_consumers.get(LogMessage, [])
    assert "system.cp.dag_assembly" in plan.system_consumers.get(
        ControlPlaneDagAssemblyRequestedEvent, []
    )
    assert ROOT_LEAF_INGRESS_SOURCE_NODE_NAME in step_names


def test_control_plane_system_plan_root_builds_single_reply_drain_source_with_specs() -> None:
    registry = InjectionRegistry()
    registry.register_factory("service", ControlPlaneConfigStreamService, lambda: _ConfigStream())
    registry.register_factory("service", ControlPlaneDiscoveryStreamService, lambda: _DiscoveryStream())
    registry.register_factory("service", ControlPlaneDiscoveryService, lambda: _Discovery())
    registry.register_factory("service", ControlPlaneSystemConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneObservabilityConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneNodeConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneConfigApplyTrackerService, lambda: _Tracker())
    registry.register_factory("service", ControlPlaneStartupConfigStore, lambda: _ConfigStore())
    registry.register_factory("service", ControlPlaneStartupBarrierService, lambda: _Barrier())
    registry.register_factory("service", ControlPlaneDagAssemblyService, lambda: _DagAssembly())
    registry.register_factory("service", ControlPlaneStateService, lambda: _State())
    registry.register_factory(
        "service",
        ControlPlaneDynamicConsumerRoutingService,
        lambda: _DynamicConsumerRouting(),
    )
    registry.register_factory("service", ControlPlaneRootLeafIngressService, lambda: _LeafIngress())
    registry.register_factory("service", ExecutionIpcHandoffDispatchService, lambda: _HandoffDispatch())
    scope = registry.instantiate_for_scenario("s-root-workers")
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 2, "nodes": []}],
        }
    }

    plan = build_control_plane_system_plan(runtime=runtime, scenario_scope=scope)
    step_names = {step.name for step in plan.system_steps}

    assert ROOT_LEAF_INGRESS_SOURCE_NODE_NAME in step_names
    source_steps = [
        step
        for step in plan.system_steps
        if step.name == ROOT_LEAF_INGRESS_SOURCE_NODE_NAME
    ]
    assert len(source_steps) == 1
    source_node = source_steps[0].step
    assert isinstance(source_node, ControlPlaneRootLeafIngressSourceNode)
    assert ("execution.alpha#1", EXECUTION_IPC_LANE_CONTROL) in source_node.ingress_specs
    assert ("execution.alpha#1", EXECUTION_IPC_LANE_DATA) in source_node.ingress_specs
    assert ("execution.alpha#2", EXECUTION_IPC_LANE_CONTROL) in source_node.ingress_specs
    assert ("execution.alpha#2", EXECUTION_IPC_LANE_DATA) in source_node.ingress_specs


def test_control_plane_system_plan_leaf_mode_includes_discovery_stage() -> None:
    registry = InjectionRegistry()
    registry.register_factory("service", ControlPlaneDiscoveryService, lambda: _Discovery())
    registry.register_factory("service", ControlPlaneConfigStreamService, lambda: _ConfigStream())
    registry.register_factory("service", ControlPlaneDiscoveryStreamService, lambda: _DiscoveryStream())
    registry.register_factory("service", ControlPlaneSystemConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneObservabilityConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneNodeConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneConfigApplyTrackerService, lambda: _Tracker())
    registry.register_factory("service", ControlPlaneStartupConfigStore, lambda: _ConfigStore())
    registry.register_factory("service", ControlPlaneStartupBarrierService, lambda: _Barrier())
    registry.register_factory("service", ControlPlaneDagAssemblyService, lambda: _DagAssembly())
    registry.register_factory("service", ControlPlaneStateService, lambda: _State())
    registry.register_factory(
        "service",
        ControlPlaneDynamicConsumerRoutingService,
        lambda: _DynamicConsumerRouting(),
    )
    registry.register_factory("service", ControlPlaneRootLeafIngressService, lambda: _LeafIngress())
    registry.register_factory("service", LeafRuntimeActivationService, lambda: _LeafActivation())
    registry.register_factory("service", LeafBoundaryExecutionService, lambda: _LeafBoundaryExecution())
    registry.register_factory(
        "service",
        ControlPlaneLeafDiscoverySnapshotApplyService,
        lambda: _LeafSnapshotApply(),
    )
    scope = registry.instantiate_for_scenario("leaf-s1")
    runtime = {
        "__process_role": "worker",
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
        },
    }

    plan = build_control_plane_system_plan(runtime=runtime, scenario_scope=scope)

    step_names = {step.name for step in plan.system_steps}
    assert set(LEAF_STATIC_SYSTEM_NODE_NAMES).issubset(step_names)
    assert "system.cp.leaf_bootstrap" in step_names
    assert "system.cp.leaf_discovery" in step_names
    assert "system.cp.leaf_snapshot_apply" in step_names
    assert "system.cp.leaf_apply_config" in step_names
    assert "system.cp.leaf_start_work" in step_names
    assert "system.cp.leaf_boundary_execute" in step_names
    assert "system.cp.leaf_stop" in step_names
    assert "system.scheduler.command" in step_names
    assert "system.scheduler.tick" in step_names
    assert "system.cp.leaf_discovery" in plan.system_consumers.get(
        ControlPlaneLeafDiscoveryRequestEvent, []
    )
    assert "system.cp.leaf_snapshot_apply" in plan.system_consumers.get(
        ControlPlaneLeafDiscoverySnapshotEvent, []
    )
    assert "system.cp.consumer_registry_discovery_apply" in plan.system_consumers.get(
        ControlPlaneLeafDiscoverySnapshotEvent,
        [],
    )
    assert "system.cp.consumer_registry_remove" in plan.system_consumers.get(
        ControlPlaneConsumerRegistryRemoveNodesEvent,
        [],
    )
    assert "system.cp.leaf_apply_config" in plan.system_consumers.get(ControlPlaneLeafConfigCardEvent, [])
    assert "system.cp.leaf_start_work" in plan.system_consumers.get(ControlPlaneLeafStartWorkEvent, [])
    assert "system.cp.leaf_bootstrap" in plan.system_consumers.get(ControlPlaneLeafPulse, [])
    assert "system.cp.leaf_stop" in plan.system_consumers.get(ControlPlaneLeafStopCommand, [])
    assert "system.cp.deferred_message_hold" in plan.system_consumers.get(
        ControlPlaneDeferredMessageHoldEvent,
        [],
    )
    assert "system.cp.deferred_message_replay" in plan.system_consumers.get(
        ControlPlaneDeferredMessageReplayRequestEvent,
        [],
    )
    assert "system.scheduler.command" in plan.system_consumers.get(PlatformSchedulerUpsertCommand, [])
    assert "system.scheduler.tick" in plan.system_consumers.get(PlatformSchedulerTickEvent, [])
    ingress_sources = {leaf_runtime_ingress_drain_source_node_name()}
    assert ingress_sources.issubset(step_names)
    assert ingress_sources.issubset(set(plan.system_consumers.get(BootstrapControl, [])))


def test_control_plane_system_plan_observability_leaf_includes_observability_lane_sources() -> None:
    registry = InjectionRegistry()
    registry.register_factory("service", ControlPlaneDiscoveryService, lambda: _Discovery())
    registry.register_factory("service", ControlPlaneConfigStreamService, lambda: _ConfigStream())
    registry.register_factory("service", ControlPlaneDiscoveryStreamService, lambda: _DiscoveryStream())
    registry.register_factory("service", ControlPlaneSystemConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneObservabilityConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneNodeConfigApplyService, lambda: _Apply())
    registry.register_factory("service", ControlPlaneConfigApplyTrackerService, lambda: _Tracker())
    registry.register_factory("service", ControlPlaneStartupConfigStore, lambda: _ConfigStore())
    registry.register_factory("service", ControlPlaneStartupBarrierService, lambda: _Barrier())
    registry.register_factory("service", ControlPlaneDagAssemblyService, lambda: _DagAssembly())
    registry.register_factory("service", ControlPlaneStateService, lambda: _State())
    registry.register_factory(
        "service",
        ControlPlaneDynamicConsumerRoutingService,
        lambda: _DynamicConsumerRouting(),
    )
    registry.register_factory("service", ControlPlaneRootLeafIngressService, lambda: _LeafIngress())
    registry.register_factory("service", LeafRuntimeActivationService, lambda: _LeafActivation())
    registry.register_factory("service", LeafBoundaryExecutionService, lambda: _LeafBoundaryExecution())
    registry.register_factory(
        "service",
        ControlPlaneLeafDiscoverySnapshotApplyService,
        lambda: _LeafSnapshotApply(),
    )
    scope = registry.instantiate_for_scenario("leaf-obs-s1")
    runtime = {
        "__process_role": "observability_worker",
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "system.observability", "workers": 1, "nodes": []}],
        },
    }

    plan = build_control_plane_system_plan(runtime=runtime, scenario_scope=scope)
    step_names = {step.name for step in plan.system_steps}
    assert "system.scheduler.command" in step_names
    assert "system.scheduler.tick" in step_names
    ingress_sources = {leaf_runtime_ingress_drain_source_node_name()}
    assert ingress_sources.issubset(step_names)
    assert ingress_sources.issubset(set(plan.system_consumers.get(BootstrapControl, [])))
