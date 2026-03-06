from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.control_plane.planning import (
    build_control_plane_system_plan,
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
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
    ControlPlaneLeafDiscoverySnapshotApplyService,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDagAssembledEvent,
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDagAssemblyRequestedEvent,
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneDiscoveryCompletedEvent,
    ControlPlaneDiscoverySourceCompletedEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneLeafPulse,
    ControlPlaneLeafStopCommand,
    ControlPlaneRootPulse,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
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


class _Bootstrapper:
    def discover_all(self, runtime):
        _ = runtime
        return []


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
    scope = registry.instantiate_for_scenario("s1")
    runtime = {
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": "execution.alpha", "workers": 1, "nodes": []}],
        }
    }

    plan = build_control_plane_system_plan(runtime=runtime, scenario_scope=scope)

    assert "system.cp.root_config_stream" in {step.name for step in plan.system_steps}
    assert "system.cp.discovery_pump" in {step.name for step in plan.system_steps}
    assert "system.cp.discovery_apply" in {step.name for step in plan.system_steps}
    assert "system.cp.discovery_finalize" in {step.name for step in plan.system_steps}
    assert "system.cp.dag_assembly" in {step.name for step in plan.system_steps}
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
    assert "system.cp.discovery_finalize" in plan.system_consumers.get(
        ControlPlaneDiscoverySourceCompletedEvent, []
    )
    assert "system.cp.startup_barrier" in {step.name for step in plan.system_steps}
    assert "system.cp.startup_barrier" in plan.system_consumers.get(
        ControlPlaneDiscoveryCompletedEvent, []
    )
    assert "system.cp.config_apply_barrier" in plan.system_consumers.get(
        ControlPlaneConfigStreamCompletedEvent, []
    )
    assert "system.cp.init_plan" in plan.system_consumers.get(
        ControlPlaneDagAssembledEvent, []
    )
    assert "system.cp.dag_assembly" in plan.system_consumers.get(
        ControlPlaneDagAssemblyRequestedEvent, []
    )


def test_control_plane_system_plan_leaf_mode_includes_discovery_stage() -> None:
    registry = InjectionRegistry()
    registry.register_factory("service", ControlPlaneBootstrapperService, lambda: _Bootstrapper())
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
    assert "system.cp.leaf_bootstrap" in step_names
    assert "system.cp.leaf_discovery" in step_names
    assert "system.cp.leaf_snapshot_apply" in step_names
    assert "system.cp.leaf_apply_config" in step_names
    assert "system.cp.leaf_start_work" in step_names
    assert "system.cp.leaf_boundary_execute" in step_names
    assert "system.cp.leaf_stop" in step_names
    assert "system.cp.leaf_discovery" in plan.system_consumers.get(
        ControlPlaneLeafDiscoveryRequestEvent, []
    )
    assert "system.cp.leaf_snapshot_apply" in plan.system_consumers.get(
        ControlPlaneLeafDiscoverySnapshotEvent, []
    )
    assert "system.cp.leaf_apply_config" in plan.system_consumers.get(ControlPlaneLeafConfigCardEvent, [])
    assert "system.cp.leaf_start_work" in plan.system_consumers.get(ControlPlaneLeafStartWorkEvent, [])
    assert "system.cp.leaf_bootstrap" in plan.system_consumers.get(ControlPlaneLeafPulse, [])
    assert "system.cp.leaf_stop" in plan.system_consumers.get(ControlPlaneLeafStopCommand, [])
