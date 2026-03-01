from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "LeafWorkerCommandLoopService",
    "DefaultLeafWorkerCommandLoopService",
    "LeafControlIngressService",
    "DefaultLeafControlIngressService",
    "LeafProcessEntryOrchestrationService",
    "DefaultLeafProcessEntryOrchestrationService",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "leaf_worker_process_entry",
    "resolve_leaf_process_entry_orchestration_service",
    "resolve_leaf_control_ingress_service",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    if name in {"LeafWorkerCommandLoopService", "DefaultLeafWorkerCommandLoopService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service")
    elif name in {"LeafControlIngressService", "DefaultLeafControlIngressService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service")
    elif name in {
        "LeafProcessEntryOrchestrationService",
        "DefaultLeafProcessEntryOrchestrationService",
        "LeafWorkerControlPlaneService",
        "DefaultLeafWorkerControlPlaneService",
        "leaf_worker_process_entry",
        "resolve_leaf_process_entry_orchestration_service",
        "resolve_leaf_control_ingress_service",
    }:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
