from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "LeafRuntimeActivationService",
    "DefaultLeafRuntimeActivationService",
    "LeafBoundaryExecutionService",
    "DefaultLeafBoundaryExecutionService",
    "LeafRuntimeBoundaryBatchService",
    "DefaultLeafRuntimeBoundaryBatchService",
    "LeafWorkerRuntimeSession",
    "bootstrap_leaf_worker_runtime_from_bundle",
    "build_leaf_hello_event",
    "execute_leaf_boundary_batch",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    if name in {"LeafRuntimeActivationService", "DefaultLeafRuntimeActivationService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service")
    elif name in {"LeafBoundaryExecutionService", "DefaultLeafBoundaryExecutionService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service")
    elif name in {"LeafRuntimeBoundaryBatchService", "DefaultLeafRuntimeBoundaryBatchService"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service")
    elif name in {
        "LeafWorkerRuntimeSession",
        "bootstrap_leaf_worker_runtime_from_bundle",
        "build_leaf_hello_event",
        "execute_leaf_boundary_batch",
    }:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
