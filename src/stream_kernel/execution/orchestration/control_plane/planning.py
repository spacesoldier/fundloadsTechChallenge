from __future__ import annotations

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.injection_registry import (
    InjectionRegistryError,
    ScenarioScope,
)
from stream_kernel.kernel.scenario import StepSpec
from .leaf.plan_builder import build_leaf_control_plane_system_plan
from .leaf.static_graph import (
    LEAF_STATIC_SYSTEM_NODE_NAMES,
)
from .root.plan_builder import build_root_control_plane_system_plan
from .root.static_graph import (
    ROOT_STATIC_SYSTEM_NODE_NAMES,
)
from .system_plan import ControlPlaneSystemPlan


def build_control_plane_system_plan(
    *,
    runtime: dict[str, object] | None,
    scenario_scope: ScenarioScope,
) -> ControlPlaneSystemPlan:
    from stream_kernel.execution.orchestration.lifecycle import runtime_bootstrap_mode

    if not isinstance(runtime, dict):
        return ControlPlaneSystemPlan()
    try:
        if runtime_bootstrap_mode(runtime) != "process_supervisor":
            return ControlPlaneSystemPlan()
    except Exception:
        return ControlPlaneSystemPlan()

    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return build_leaf_control_plane_system_plan(
            runtime=runtime,
            scenario_scope=scenario_scope,
            resolve_required_service=_resolve_required_service,
            resolve_optional_service=_resolve_optional_service,
            inject_control_plane_steps=_inject_control_plane_steps,
            leaf_runtime_activation_contract=_leaf_runtime_activation_contract,
            leaf_boundary_execution_contract=_leaf_boundary_execution_contract,
            leaf_snapshot_apply_contract=_leaf_snapshot_apply_contract,
            leaf_shutdown_readiness_contract=_leaf_shutdown_readiness_contract,
            noop_leaf_shutdown_readiness_service=_noop_leaf_shutdown_readiness_service,
        )
    return build_root_control_plane_system_plan(
        runtime=runtime,
        scenario_scope=scenario_scope,
        resolve_required_service=_resolve_required_service,
        resolve_optional_service=_resolve_optional_service,
        inject_control_plane_steps=_inject_control_plane_steps,
        root_boundary_handoff_contract=_root_boundary_handoff_contract,
        root_leaf_ingress_contract=_root_leaf_ingress_contract,
        root_lifecycle_orchestration_contract=_root_lifecycle_orchestration_contract,
        root_lifecycle_log_factory_contract=_root_lifecycle_log_factory_contract,
        root_lifecycle_console_dispatch_contract=_root_lifecycle_console_dispatch_contract,
        shutdown_readiness_contract=_shutdown_readiness_contract,
        noop_shutdown_readiness_service=_noop_shutdown_readiness_service,
    )


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


def _inject_control_plane_steps(steps: list[StepSpec], scope: ScenarioScope) -> None:
    for spec in steps:
        apply_injection(spec.step, scope, False)


__all__ = [
    "ControlPlaneSystemPlan",
    "LEAF_STATIC_SYSTEM_NODE_NAMES",
    "ROOT_STATIC_SYSTEM_NODE_NAMES",
    "build_control_plane_system_plan",
]


def _leaf_runtime_activation_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service import (
        LeafRuntimeActivationService,
    )

    return LeafRuntimeActivationService


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


def _root_boundary_handoff_contract() -> type[object]:
    from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
        ControlPlaneRootBoundaryHandoffService,
    )

    return ControlPlaneRootBoundaryHandoffService


def _root_leaf_ingress_contract() -> type[object]:
    from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service import (
        ControlPlaneRootLeafIngressService,
    )

    return ControlPlaneRootLeafIngressService


def _root_lifecycle_orchestration_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service import (
        ControlPlaneLifecycleOrchestrationService,
    )

    return ControlPlaneLifecycleOrchestrationService


def _root_lifecycle_log_factory_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.root.startup.log_factory_service import (
        RootLifecycleLogFactory,
    )

    return RootLifecycleLogFactory


def _root_lifecycle_console_dispatch_contract() -> type[object]:
    from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
        RootConsoleLogDispatchService,
    )

    return RootConsoleLogDispatchService


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
