from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "LeafProcessEntryOrchestrationService",
    "DefaultLeafProcessEntryOrchestrationService",
    "LeafWorkerControlPlaneService",
    "DefaultLeafWorkerControlPlaneService",
    "leaf_worker_process_entry",
    "resolve_leaf_process_entry_orchestration_service",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    if name in {
        "LeafProcessEntryOrchestrationService",
        "DefaultLeafProcessEntryOrchestrationService",
        "LeafWorkerControlPlaneService",
        "DefaultLeafWorkerControlPlaneService",
        "leaf_worker_process_entry",
        "resolve_leaf_process_entry_orchestration_service",
    }:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
