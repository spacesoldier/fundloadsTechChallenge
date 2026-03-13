from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ChildRuntimeBootstrapError",
    "ChildBootstrapBundle",
    "ChildRuntimeBootstrap",
    "ChildBoundaryInput",
    "build_child_bootstrap_bundle",
    "LeafRuntimeBootstrapService",
    "LeafRuntimeBootstrapAssemblyResult",
    "LeafRuntimeBootstrapAssemblyService",
    "DefaultLeafRuntimeBootstrapAssemblyService",
    "DefaultLeafRuntimeBootstrapService",
    "LeafRuntimeStepAssemblyService",
    "DefaultLeafRuntimeStepAssemblyService",
    "LeafRuntimeStepAssemblyResult",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    if name in {"ChildRuntimeBootstrapError", "ChildBootstrapBundle", "ChildRuntimeBootstrap", "ChildBoundaryInput"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models")
    elif name in {"build_child_bootstrap_bundle"}:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_bundle")
    elif name in {
        "LeafRuntimeBootstrapService",
        "LeafRuntimeBootstrapAssemblyResult",
        "LeafRuntimeBootstrapAssemblyService",
        "DefaultLeafRuntimeBootstrapAssemblyService",
        "DefaultLeafRuntimeBootstrapService",
    }:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service")
    elif name in {
        "LeafRuntimeStepAssemblyService",
        "DefaultLeafRuntimeStepAssemblyService",
        "LeafRuntimeStepAssemblyResult",
    }:
        mod = import_module("stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_step_assembly_service")
    else:
        raise AttributeError(name)
    value = getattr(mod, name)
    globals()[name] = value
    return value
