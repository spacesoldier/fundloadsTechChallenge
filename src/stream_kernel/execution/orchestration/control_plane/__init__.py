from __future__ import annotations

from importlib import import_module

_PLANNING_EXPORTS = {
    "ControlPlaneSystemPlan",
    "build_control_plane_system_plan",
}
_ROOT_NODE_EXPORTS = {
    "ControlPlaneConfigApplyBarrierNode",
    "ControlPlaneDagAssemblyNode",
    "ControlPlaneDiscoveryApplyNode",
    "ControlPlaneDiscoveryMaterializeNode",
    "ControlPlaneDiscoveryFinalizeNode",
    "ControlPlaneDiscoveryPumpNode",
    "ControlPlaneInitPlanNode",
    "ControlPlaneStartWorkReadinessNode",
    "ControlPlaneStartWorkDispatchNode",
    "ControlPlaneNodeConfigApplyNode",
    "ControlPlaneObservabilityConfigApplyNode",
    "ControlPlaneRootConfigStreamNode",
    "ControlPlaneRootLeafBoundaryDispatchNode",
    "ControlPlaneRootLeafStartWorkDispatchNode",
    "ControlPlaneRootLeafConfigAckNode",
    "ControlPlaneRootLeafConfigAssignNode",
    "ControlPlaneRootLeafDrainReadyNode",
    "ControlPlaneRootLeafStopAckNode",
    "ControlPlaneRootLeafStopDispatchNode",
    "ControlPlaneRootBootstrapNode",
    "ControlPlaneSystemConfigApplyNode",
    "ControlPlaneStartupBarrierNode",
}
_LEAF_NODE_EXPORTS = {
    "ControlPlaneLeafApplyConfigNode",
    "ControlPlaneLeafDiscoveryRequestNode",
    "ControlPlaneLeafSnapshotApplyNode",
    "ControlPlaneLeafStartWorkNode",
    "ControlPlaneLeafBoundaryExecuteNode",
    "ControlPlaneLeafTombstoneFinalizeNode",
    "ControlPlaneLeafBootstrapNode",
    "ControlPlaneLeafConfigApplyRuntimeNode",
    "ControlPlaneLeafStopNode",
}
_SHARED_INIT_NODE_EXPORTS = {
    "ControlPlaneInitializationDispatchNode",
    "ControlPlaneInitializationPlanNode",
    "ControlPlaneNodeInitializeNode",
    "ControlPlaneReadyForWorkNode",
}

_ROOT_LEAF_INGRESS_EXPORTS = {
    "ControlPlaneRootLeafIngressService",
    "DefaultControlPlaneRootLeafIngressService",
}
_ROOT_SNAPSHOT_EXPORTS = {
    "ControlPlaneRootDiscoverySnapshotService",
    "DefaultControlPlaneRootDiscoverySnapshotService",
}
_ROOT_RUNTIME_BOOTSTRAP_EXPORTS = {
    "ControlPlaneRootRuntimeBootstrapService",
    "DefaultControlPlaneRootRuntimeBootstrapService",
}
_ROOT_BOUNDARY_EXEC_EXPORTS = {
    "ControlPlaneRootBoundaryExecutionService",
    "DefaultControlPlaneRootBoundaryExecutionService",
}
_ROOT_BOUNDARY_HANDOFF_EXPORTS = {
    "ControlPlaneRootBoundaryHandoffService",
    "DefaultControlPlaneRootBoundaryHandoffService",
}

__all__ = sorted(
    _PLANNING_EXPORTS
    | _ROOT_NODE_EXPORTS
    | _LEAF_NODE_EXPORTS
    | _SHARED_INIT_NODE_EXPORTS
    | _ROOT_LEAF_INGRESS_EXPORTS
    | _ROOT_SNAPSHOT_EXPORTS
    | _ROOT_RUNTIME_BOOTSTRAP_EXPORTS
    | _ROOT_BOUNDARY_EXEC_EXPORTS
    | _ROOT_BOUNDARY_HANDOFF_EXPORTS
)


def __getattr__(name: str) -> object:
    if name in _PLANNING_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.planning")
    elif name in _ROOT_NODE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.system_nodes")
    elif name in _LEAF_NODE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.leaf.system_nodes")
    elif name in _SHARED_INIT_NODE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.initialization_nodes")
    elif name in _ROOT_LEAF_INGRESS_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_service")
    elif name in _ROOT_SNAPSHOT_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service")
    elif name in _ROOT_RUNTIME_BOOTSTRAP_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service")
    elif name in _ROOT_BOUNDARY_EXEC_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service")
    elif name in _ROOT_BOUNDARY_HANDOFF_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
