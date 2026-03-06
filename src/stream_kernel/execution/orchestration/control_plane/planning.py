from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stream_kernel.application_context.injection_registry import (
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.kernel.scenario import StepSpec
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
    ControlPlaneLeafDiscoveryRequestEvent,
    ControlPlaneLeafDiscoverySnapshotEvent,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneLeafPulse,
    ControlPlaneRootPulse,
)
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)

from .leaf.system_nodes import (
    ControlPlaneLeafBootstrapNode,
    ControlPlaneLeafBoundaryExecuteNode,
    ControlPlaneLeafConfigApplyRuntimeNode,
    ControlPlaneLeafDiscoveryRequestNode,
    ControlPlaneLeafStartWorkNode,
    ControlPlaneLeafSnapshotApplyNode,
    ControlPlaneLeafStopNode,
    ControlPlaneLeafTombstoneFinalizeNode,
)
from .root.system_nodes import (
    ControlPlaneConfigApplyBarrierNode,
    ControlPlaneDagAssemblyNode,
    ControlPlaneDiscoveryApplyNode,
    ControlPlaneDiscoveryFinalizeNode,
    ControlPlaneDiscoveryPumpNode,
    ControlPlaneInitPlanNode,
    ControlPlaneNodeConfigApplyNode,
    ControlPlaneObservabilityConfigApplyNode,
    ControlPlaneRootBootstrapNode,
    ControlPlaneRootConfigStreamNode,
    ControlPlaneRootLeafDrainReadyNode,
    ControlPlaneRootTombstoneObservedNode,
    ControlPlaneStartWorkDispatchNode,
    ControlPlaneStartupBarrierNode,
    ControlPlaneSystemConfigApplyNode,
)


@dataclass(frozen=True, slots=True)
class ControlPlaneSystemPlan:
    system_steps: list[StepSpec] = field(default_factory=list)
    system_consumers: dict[type[Any], list[str]] = field(default_factory=dict)
    system_node_names: set[str] = field(default_factory=set)


def build_control_plane_system_plan(
    *,
    runtime: dict[str, object] | None,
    scenario_scope: ScenarioScope,
) -> ControlPlaneSystemPlan:
    from stream_kernel.execution.orchestration.lifecycle import runtime_bootstrap_mode
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneConfigApplyCompletedEvent,
        ControlPlaneConfigStreamCompletedEvent,
        ControlPlaneDagAssembledEvent,
        ControlPlaneDagAssemblyRequestedEvent,
        ControlPlaneDiscoveryBatchReadyEvent,
        ControlPlaneDiscoveryBatchRequestedEvent,
        ControlPlaneDiscoveryCompletedEvent,
        ControlPlaneDiscoverySourceCompletedEvent,
        ControlPlaneDiscoveryStartRequestedEvent,
        ControlPlaneLeafBoundaryExecuteCommand,
        ControlPlaneLeafBoundaryResultEvent,
        ControlPlaneLeafDrainReadyEvent,
        ControlPlaneLeafConfigCardEvent,
        ControlPlaneLeafStartWorkEvent,
        ControlPlaneLeafPulse,
        ControlPlaneLeafStopCommand,
        ControlPlaneNodeConfigAppliedEvent,
        ControlPlaneObservabilityConfigAppliedEvent,
        ControlPlaneRootPulse,
        ControlPlaneStartWorkEvent,
        ControlPlaneSystemConfigAppliedEvent,
        NodeConfigRecord,
        ObservabilityConfigRecord,
        SystemRuntimeConfigRecord,
    )

    if not isinstance(runtime, dict):
        return ControlPlaneSystemPlan()
    try:
        if runtime_bootstrap_mode(runtime) != "process_supervisor":
            return ControlPlaneSystemPlan()
    except Exception:
        return ControlPlaneSystemPlan()

    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        leaf = ControlPlaneLeafBootstrapNode()
        leaf_apply = ControlPlaneLeafConfigApplyRuntimeNode(
            activation=_resolve_required_service(
                scope=scenario_scope,
                contract=_leaf_runtime_activation_contract(),
                method_name="apply_config",
            )
        )
        leaf_discovery = ControlPlaneLeafDiscoveryRequestNode(
            bootstrapper=_resolve_required_service(
                scope=scenario_scope,
                contract=_leaf_bootstrapper_contract(),
                method_name="discover_all",
            ),
            discovery=_resolve_required_service(
                scope=scenario_scope,
                contract=ControlPlaneDiscoveryService,
                method_name="append_item",
            ),
        )
        leaf_snapshot = ControlPlaneLeafSnapshotApplyNode(
            snapshot_apply=_resolve_required_service(
                scope=scenario_scope,
                contract=_leaf_snapshot_apply_contract(),
                method_name="apply_snapshot",
            )
        )
        leaf_boundary = ControlPlaneLeafBoundaryExecuteNode(
            boundary_execution=_resolve_required_service(
                scope=scenario_scope,
                contract=_leaf_boundary_execution_contract(),
                method_name="execute",
            )
        )
        leaf_start_work = ControlPlaneLeafStartWorkNode()
        leaf_stop = ControlPlaneLeafStopNode()
        leaf_tombstone_finalize = ControlPlaneLeafTombstoneFinalizeNode(
            readiness=_resolve_optional_service(
                scope=scenario_scope,
                contract=_leaf_shutdown_readiness_contract(),
                method_name="observe_boundary_result",
            ) or _noop_leaf_shutdown_readiness_service()
        )
        return ControlPlaneSystemPlan(
            system_steps=[
                StepSpec(name="system.cp.leaf_bootstrap", step=leaf),
                StepSpec(name="system.cp.leaf_discovery", step=leaf_discovery),
                StepSpec(name="system.cp.leaf_snapshot_apply", step=leaf_snapshot),
                StepSpec(name="system.cp.leaf_apply_config", step=leaf_apply),
                StepSpec(name="system.cp.leaf_start_work", step=leaf_start_work),
                StepSpec(name="system.cp.leaf_boundary_execute", step=leaf_boundary),
                StepSpec(name="system.cp.leaf_tombstone_finalize", step=leaf_tombstone_finalize),
                StepSpec(name="system.cp.leaf_stop", step=leaf_stop),
            ],
            system_consumers={
                ControlPlaneLeafPulse: ["system.cp.leaf_bootstrap"],
                ControlPlaneLeafDiscoveryRequestEvent: ["system.cp.leaf_discovery"],
                ControlPlaneLeafDiscoverySnapshotEvent: ["system.cp.leaf_snapshot_apply"],
                ControlPlaneLeafConfigCardEvent: ["system.cp.leaf_apply_config"],
                ControlPlaneLeafStartWorkEvent: ["system.cp.leaf_start_work"],
                ControlPlaneLeafBoundaryExecuteCommand: ["system.cp.leaf_boundary_execute"],
                ControlPlaneLeafBoundaryResultEvent: ["system.cp.leaf_tombstone_finalize"],
                ControlPlaneLeafStopCommand: ["system.cp.leaf_stop"],
            },
            system_node_names={
                "system.cp.leaf_bootstrap",
                "system.cp.leaf_discovery",
                "system.cp.leaf_snapshot_apply",
                "system.cp.leaf_apply_config",
                "system.cp.leaf_start_work",
                "system.cp.leaf_boundary_execute",
                "system.cp.leaf_tombstone_finalize",
                "system.cp.leaf_stop",
            },
        )

    root_bootstrap = ControlPlaneRootBootstrapNode()
    root_config_stream = ControlPlaneRootConfigStreamNode(
        config_stream=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneConfigStreamService,
            method_name="stream",
        )
    )
    discovery_pump = ControlPlaneDiscoveryPumpNode(
        stream=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDiscoveryStreamService,
            method_name="start",
        )
    )
    discovery_apply = ControlPlaneDiscoveryApplyNode(
        discovery=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDiscoveryService,
            method_name="append_item",
        )
    )
    discovery_finalize = ControlPlaneDiscoveryFinalizeNode()
    system_config_apply = ControlPlaneSystemConfigApplyNode(
        applier=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneSystemConfigApplyService,
            method_name="apply",
        )
    )
    observability_config_apply = ControlPlaneObservabilityConfigApplyNode(
        applier=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneObservabilityConfigApplyService,
            method_name="apply",
        )
    )
    node_config_apply = ControlPlaneNodeConfigApplyNode(
        applier=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneNodeConfigApplyService,
            method_name="apply",
        )
    )
    config_apply_barrier = ControlPlaneConfigApplyBarrierNode(
        tracker=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneConfigApplyTrackerService,
            method_name="mark_stream_completed",
        ),
        config_store=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStartupConfigStore,
            method_name="records",
        ),
    )
    startup_barrier = ControlPlaneStartupBarrierNode(
        barrier=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStartupBarrierService,
            method_name="mark_discovery_completed",
        )
    )
    dag_assembly = ControlPlaneDagAssemblyNode(
        assembly=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneDagAssemblyService,
            method_name="assemble",
        ),
    )
    init_plan = ControlPlaneInitPlanNode(
        state=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
    )
    start_work_dispatch = ControlPlaneStartWorkDispatchNode(
        state=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
        handoff_dispatch=_resolve_optional_service(
            scope=scenario_scope,
            contract=_transport_handoff_dispatch_contract(),
            method_name="dispatch_broadcast",
        ),
    )
    tombstone_observed = ControlPlaneRootTombstoneObservedNode(
        state=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
        shutdown_readiness=_resolve_optional_service(
            scope=scenario_scope,
            contract=_shutdown_readiness_contract(),
            method_name="observe_tombstone",
        ) or _noop_shutdown_readiness_service(),
    )
    leaf_drain_ready = ControlPlaneRootLeafDrainReadyNode(
        state=_resolve_required_service(
            scope=scenario_scope,
            contract=ControlPlaneStateService,
            method_name="append_event",
        ),
        shutdown_readiness=_resolve_optional_service(
            scope=scenario_scope,
            contract=_shutdown_readiness_contract(),
            method_name="mark_leaf_ready",
        ) or _noop_shutdown_readiness_service(),
    )
    return ControlPlaneSystemPlan(
        system_steps=[
            StepSpec(name="system.cp.root_bootstrap", step=root_bootstrap),
            StepSpec(name="system.cp.root_config_stream", step=root_config_stream),
            StepSpec(name="system.cp.discovery_pump", step=discovery_pump),
            StepSpec(name="system.cp.discovery_apply", step=discovery_apply),
            StepSpec(name="system.cp.discovery_finalize", step=discovery_finalize),
            StepSpec(name="system.cp.system_config_apply", step=system_config_apply),
            StepSpec(name="system.cp.observability_config_apply", step=observability_config_apply),
            StepSpec(name="system.cp.node_config_apply", step=node_config_apply),
            StepSpec(name="system.cp.config_apply_barrier", step=config_apply_barrier),
            StepSpec(name="system.cp.startup_barrier", step=startup_barrier),
            StepSpec(name="system.cp.dag_assembly", step=dag_assembly),
            StepSpec(name="system.cp.init_plan", step=init_plan),
            StepSpec(name="system.cp.start_work_dispatch", step=start_work_dispatch),
            StepSpec(name="system.cp.shutdown_tombstone_observed", step=tombstone_observed),
            StepSpec(name="system.cp.shutdown_leaf_ready", step=leaf_drain_ready),
        ],
        system_consumers={
            ControlPlaneRootPulse: ["system.cp.root_bootstrap", "system.cp.root_config_stream"],
            ControlPlaneStartWorkEvent: ["system.cp.start_work_dispatch"],
            ControlPlaneDiscoveryStartRequestedEvent: ["system.cp.discovery_pump"],
            ControlPlaneDiscoveryBatchRequestedEvent: ["system.cp.discovery_pump"],
            ControlPlaneDiscoveryBatchReadyEvent: ["system.cp.discovery_apply"],
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
            ControlPlaneLeafBoundaryResultEvent: ["system.cp.shutdown_tombstone_observed"],
            ControlPlaneLeafDrainReadyEvent: ["system.cp.shutdown_leaf_ready"],
        },
        system_node_names={
            "system.cp.root_bootstrap",
            "system.cp.root_config_stream",
            "system.cp.discovery_pump",
            "system.cp.discovery_apply",
            "system.cp.discovery_finalize",
            "system.cp.system_config_apply",
            "system.cp.observability_config_apply",
            "system.cp.node_config_apply",
            "system.cp.config_apply_barrier",
            "system.cp.startup_barrier",
            "system.cp.dag_assembly",
            "system.cp.init_plan",
            "system.cp.start_work_dispatch",
            "system.cp.shutdown_tombstone_observed",
            "system.cp.shutdown_leaf_ready",
        },
    )


def control_plane_bootstrap_inputs(
    *,
    runtime: dict[str, object] | None,
    inputs: list[object] | tuple[object, ...],
) -> list[object]:
    resolved = list(inputs)
    if _should_emit_control_plane_root_pulse(runtime):
        resolved.insert(0, ControlPlaneRootPulse(runtime=runtime if isinstance(runtime, dict) else {}))
    if _should_emit_control_plane_leaf_pulse(runtime):
        resolved.insert(0, ControlPlaneLeafPulse(runtime=runtime if isinstance(runtime, dict) else {}))
    return resolved


def should_include_business_steps(runtime: dict[str, object] | None) -> bool:
    # Root process-supervisor run is control-plane first and excludes business graph steps.
    return not _is_root_process_supervisor_runtime(runtime)


def _should_emit_control_plane_root_pulse(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return False
    return _is_process_supervisor_mode(runtime)


def _should_emit_control_plane_leaf_pulse(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    process_role = runtime.get("__process_role")
    if not isinstance(process_role, str) or process_role not in {"worker", "observability_worker"}:
        return False
    return _is_process_supervisor_mode(runtime)


def _is_root_process_supervisor_runtime(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return False
    return _is_process_supervisor_mode(runtime)


def _is_process_supervisor_mode(runtime: dict[str, object]) -> bool:
    from stream_kernel.execution.orchestration.lifecycle import runtime_bootstrap_mode

    try:
        return runtime_bootstrap_mode(runtime) == "process_supervisor"
    except Exception:
        return False


def _resolve_required_service(
    *,
    scope: ScenarioScope,
    contract: type[object],
    method_name: str,
) -> object:
    try:
        resolved = scope.resolve("service", contract)
    except InjectionRegistryError as exc:
        raise InjectionRegistryError(
            f"Missing required control-plane startup service: {contract.__name__}"
        ) from exc
    if isinstance(resolved, contract):
        return resolved
    if callable(getattr(resolved, method_name, None)):
        return resolved
    raise InjectionRegistryError(
        "Invalid control-plane startup service binding: "
        f"{contract.__name__} must provide method '{method_name}'"
    )


def _resolve_optional_service(
    *,
    scope: ScenarioScope,
    contract: type[object],
    method_name: str,
) -> object | None:
    try:
        resolved = scope.resolve("service", contract)
    except InjectionRegistryError:
        return None
    if isinstance(resolved, contract):
        return resolved
    if callable(getattr(resolved, method_name, None)):
        return resolved
    return None


__all__ = [
    "ControlPlaneSystemPlan",
    "build_control_plane_system_plan",
    "control_plane_bootstrap_inputs",
    "should_include_business_steps",
]


def _leaf_runtime_activation_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        LeafRuntimeActivationService,
    )

    return LeafRuntimeActivationService


def _leaf_bootstrapper_contract() -> type[object]:
    from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
        ControlPlaneBootstrapperService,
    )

    return ControlPlaneBootstrapperService


def _leaf_boundary_execution_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service import (
        LeafBoundaryExecutionService,
    )

    return LeafBoundaryExecutionService


def _leaf_snapshot_apply_contract() -> type[object]:
    from stream_kernel.platform.services.runtime.control_plane_discovery_snapshot import (
        ControlPlaneLeafDiscoverySnapshotApplyService,
    )

    return ControlPlaneLeafDiscoverySnapshotApplyService


def _transport_handoff_dispatch_contract() -> type[object]:
    from stream_kernel.execution.transport.handoff.ipc_handoff_dispatch_service import (
        ExecutionIpcHandoffDispatchService,
    )

    return ExecutionIpcHandoffDispatchService


def _shutdown_readiness_contract() -> type[object]:
    from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
        ControlPlaneShutdownReadinessService,
    )

    return ControlPlaneShutdownReadinessService


def _leaf_shutdown_readiness_contract() -> type[object]:
    from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
        ControlPlaneLeafShutdownReadinessService,
    )

    return ControlPlaneLeafShutdownReadinessService


def _noop_shutdown_readiness_service() -> object:
    from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
        ControlPlaneShutdownReadinessSnapshot,
    )

    class _NoopShutdownReadiness:
        def configure_expected_groups(self, groups: tuple[str, ...]) -> None:  # noqa: ARG002
            return None

        def observe_tombstone(self, result: object) -> ControlPlaneShutdownReadinessSnapshot:  # noqa: ARG002
            return ControlPlaneShutdownReadinessSnapshot()

        def mark_leaf_ready(
            self,
            event: object,  # noqa: ARG002
        ) -> tuple[bool, ControlPlaneShutdownReadinessSnapshot]:
            return (False, ControlPlaneShutdownReadinessSnapshot())

        def snapshot(self) -> ControlPlaneShutdownReadinessSnapshot:
            return ControlPlaneShutdownReadinessSnapshot()

    return _NoopShutdownReadiness()


def _noop_leaf_shutdown_readiness_service() -> object:
    class _NoopLeafShutdownReadiness:
        def observe_boundary_result(self, result: object) -> None:  # noqa: ARG002
            return None

    return _NoopLeafShutdownReadiness()
