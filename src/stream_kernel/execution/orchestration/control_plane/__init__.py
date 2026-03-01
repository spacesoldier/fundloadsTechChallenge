from __future__ import annotations

from importlib import import_module

_PLANNING_EXPORTS = {
    "ControlPlaneSystemPlan",
    "build_control_plane_system_plan",
    "control_plane_bootstrap_inputs",
    "should_include_business_steps",
}
_ROOT_NODE_EXPORTS = {
    "ControlPlaneConfigApplyBarrierNode",
    "ControlPlaneDagAssemblyNode",
    "ControlPlaneDiscoveryApplyNode",
    "ControlPlaneDiscoveryFinalizeNode",
    "ControlPlaneDiscoveryPumpNode",
    "ControlPlaneInitPlanNode",
    "ControlPlaneNodeConfigApplyNode",
    "ControlPlaneObservabilityConfigApplyNode",
    "ControlPlaneRootConfigStreamNode",
    "ControlPlaneRootLeafBoundaryDispatchNode",
    "ControlPlaneRootLeafBoundaryResultNode",
    "ControlPlaneRootLeafConfigAckNode",
    "ControlPlaneRootLeafConfigAssignNode",
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
    "ControlPlaneLeafBoundaryExecuteNode",
    "ControlPlaneLeafBootstrapNode",
    "ControlPlaneLeafConfigApplyRuntimeNode",
    "ControlPlaneLeafStopNode",
}

_ROOT_LEAF_COMMAND_EXPORTS = {
    "ControlPlaneRootLeafCommandService",
    "DefaultControlPlaneRootLeafCommandService",
}
_ROOT_REPLY_INGRESS_EXPORTS = {
    "ControlPlaneRootReplyIngressService",
    "DefaultControlPlaneRootReplyIngressService",
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
_ROOT_STOP_EXEC_EXPORTS = {
    "ControlPlaneRootStopExecutionService",
    "DefaultControlPlaneRootStopExecutionService",
}
_ROOT_SHUTDOWN_EXPORTS = {
    "ControlPlaneRootShutdownService",
    "DefaultControlPlaneRootShutdownService",
    "ControlPlaneRootShutdownResult",
}

__all__ = sorted(
    _PLANNING_EXPORTS
    | _ROOT_NODE_EXPORTS
    | _LEAF_NODE_EXPORTS
    | _ROOT_LEAF_COMMAND_EXPORTS
    | _ROOT_REPLY_INGRESS_EXPORTS
    | _ROOT_SNAPSHOT_EXPORTS
    | _ROOT_RUNTIME_BOOTSTRAP_EXPORTS
    | _ROOT_BOUNDARY_EXEC_EXPORTS
    | _ROOT_BOUNDARY_HANDOFF_EXPORTS
    | _ROOT_STOP_EXEC_EXPORTS
    | _ROOT_SHUTDOWN_EXPORTS
)


def __getattr__(name: str) -> object:
    if name in _PLANNING_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.planning")
    elif name in _ROOT_NODE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.system_nodes")
    elif name in _LEAF_NODE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.leaf.system_nodes")
    elif name in _ROOT_LEAF_COMMAND_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.leaf_command_service")
    elif name in _ROOT_REPLY_INGRESS_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service")
    elif name in _ROOT_SNAPSHOT_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service")
    elif name in _ROOT_RUNTIME_BOOTSTRAP_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service")
    elif name in _ROOT_BOUNDARY_EXEC_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service")
    elif name in _ROOT_BOUNDARY_HANDOFF_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service")
    elif name in _ROOT_STOP_EXEC_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.stop_execution_service")
    elif name in _ROOT_SHUTDOWN_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.control_plane.root.shutdown_service")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
