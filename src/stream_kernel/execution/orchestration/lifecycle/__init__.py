from __future__ import annotations

from importlib import import_module

_ORCH_EXPORTS = {
    "_build_boundary_dispatch_inputs",
    "BoundaryDispatchInput",
    "RuntimeExecutionError",
    "RuntimeLifecyclePolicy",
    "RuntimeLifecycleReadyError",
    "RuntimeLifecycleResolutionError",
    "RuntimeWorkerFailedError",
    "execute_with_runtime_lifecycle",
    "resolve_runtime_lifecycle_manager",
    "runtime_bootstrap_mode",
    "runtime_lifecycle_policy",
    "runtime_process_group_names",
}

_SERVICE_EXPORTS = {
    "ControlPlaneLifecycleOrchestrationService",
    "DefaultControlPlaneLifecycleOrchestrationService",
    "ControlPlaneRootRuntimeLifecycleManager",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "LeafRuntimeActivationService",
    "DefaultLeafRuntimeActivationService",
    "LeafBoundaryExecutionService",
    "DefaultLeafBoundaryExecutionService",
    "LeafRuntimeBootstrapService",
    "DefaultLeafRuntimeBootstrapService",
    "LeafRuntimeBoundaryBatchService",
    "DefaultLeafRuntimeBoundaryBatchService",
}

_SYSTEM_EXPORTS = {
    "ControlPlaneGroupStartupWaitNode",
    "ControlPlaneSpawnDispatchNode",
}

_PLANNING_EXPORTS = {
    "LifecycleSystemPlan",
    "build_lifecycle_system_plan",
}

__all__ = sorted(_ORCH_EXPORTS | _SERVICE_EXPORTS | _SYSTEM_EXPORTS | _PLANNING_EXPORTS)


def __getattr__(name: str) -> object:
    if name in _ORCH_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.orchestration")
        value = getattr(mod, name)
        if name == "BoundaryDispatchInput":
            try:
                value.__module__ = __name__
            except Exception:
                pass
        globals()[name] = value
        return value
    if name in _SERVICE_EXPORTS:
        if name in {"LeafWorkerControlPlaneService", "DefaultLeafWorkerControlPlaneService"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service")
        elif name in {"LeafRuntimeActivationService", "DefaultLeafRuntimeActivationService"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service")
        elif name in {"LeafBoundaryExecutionService", "DefaultLeafBoundaryExecutionService"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service")
        elif name in {"LeafRuntimeBootstrapService", "DefaultLeafRuntimeBootstrapService"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service")
        elif name in {"LeafRuntimeBoundaryBatchService", "DefaultLeafRuntimeBoundaryBatchService"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service")
        elif name in {"ControlPlaneRootRuntimeLifecycleManager"}:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager")
        else:
            mod = import_module("stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    if name in _SYSTEM_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.root.startup.system_nodes")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    if name in _PLANNING_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.root.startup.planning")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
