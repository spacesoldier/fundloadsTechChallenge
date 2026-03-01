from __future__ import annotations

from importlib import import_module

_COMMAND_LOOP_EXPORTS = {
    "LeafWorkerCommandLoopService",
    "DefaultLeafWorkerCommandLoopService",
    "LeafControlIngressService",
    "DefaultLeafControlIngressService",
}
_CONTROL_PLANE_EXPORTS = {
    "LeafProcessEntryOrchestrationService",
    "DefaultLeafProcessEntryOrchestrationService",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "leaf_worker_process_entry",
    "resolve_leaf_process_entry_orchestration_service",
    "resolve_leaf_control_ingress_service",
}
_ACTIVATION_EXPORTS = {
    "LeafRuntimeActivationService",
    "DefaultLeafRuntimeActivationService",
}
_BOUNDARY_EXPORTS = {
    "LeafBoundaryExecutionService",
    "DefaultLeafBoundaryExecutionService",
}
_BOOTSTRAP_EXPORTS = {
    "LeafRuntimeBootstrapService",
    "DefaultLeafRuntimeBootstrapService",
}
_STARTUP_MODEL_EXPORTS = {
    "ChildRuntimeBootstrapError",
    "ChildBootstrapBundle",
    "ChildRuntimeBootstrap",
    "ChildBoundaryInput",
}
_RUNTIME_BOUNDARY_EXPORTS = {
    "LeafRuntimeBoundaryBatchService",
    "DefaultLeafRuntimeBoundaryBatchService",
}
_RUNTIME_EXPORTS = {
    "LeafWorkerRuntimeSession",
    "bootstrap_leaf_worker_runtime_from_bundle",
    "build_leaf_hello_event",
    "execute_leaf_boundary_batch",
}
_CHILD_BUNDLE_EXPORTS = {
    "is_child_bootstrap_bundle",
    "project_child_bundle_for_group",
    "resolve_child_process_role",
}

__all__ = sorted(
    _COMMAND_LOOP_EXPORTS
    | _CONTROL_PLANE_EXPORTS
    | _ACTIVATION_EXPORTS
    | _BOUNDARY_EXPORTS
    | _BOOTSTRAP_EXPORTS
    | _STARTUP_MODEL_EXPORTS
    | _RUNTIME_BOUNDARY_EXPORTS
    | _RUNTIME_EXPORTS
    | _CHILD_BUNDLE_EXPORTS
)


def __getattr__(name: str) -> object:
    if name in {"LeafWorkerCommandLoopService", "DefaultLeafWorkerCommandLoopService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service")
    elif name in {"LeafControlIngressService", "DefaultLeafControlIngressService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service")
    elif name in _CONTROL_PLANE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service")
    elif name in _ACTIVATION_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service")
    elif name in _BOUNDARY_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service")
    elif name in _BOOTSTRAP_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service")
    elif name in _STARTUP_MODEL_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models")
    elif name in _RUNTIME_BOUNDARY_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service")
    elif name in _RUNTIME_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime")
    elif name in _CHILD_BUNDLE_EXPORTS:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.child_bundle")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
