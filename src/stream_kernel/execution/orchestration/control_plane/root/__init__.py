from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    'ControlPlaneRootRunnerControlService': 'channel_services',
    'DefaultControlPlaneRootRunnerControlService': 'channel_services',
    'ControlPlaneRootLeafCommandService': 'leaf_command_service',
    'DefaultControlPlaneRootLeafCommandService': 'leaf_command_service',
    'ControlPlaneRootLeafIngressService': 'leaf_ingress_service',
    'DefaultControlPlaneRootLeafIngressService': 'leaf_ingress_service',
    'ControlPlaneRootRuntimeBootstrapService': 'runtime_bootstrap_service',
    'DefaultControlPlaneRootRuntimeBootstrapService': 'runtime_bootstrap_service',
    'ControlPlaneRootBoundaryExecutionService': 'boundary_execution_service',
    'DefaultControlPlaneRootBoundaryExecutionService': 'boundary_execution_service',
    'ControlPlaneRootBoundaryExecutionTimeoutError': 'boundary_execution_service',
    'ControlPlaneRootBoundaryExecutionFailedError': 'boundary_execution_service',
    'ControlPlaneRootBoundaryHandoffService': 'boundary_handoff_service',
    'DefaultControlPlaneRootBoundaryHandoffService': 'boundary_handoff_service',
    'ControlPlaneRootDiscoverySnapshotService': 'discovery_snapshot_service',
    'DefaultControlPlaneRootDiscoverySnapshotService': 'discovery_snapshot_service',
}


def __getattr__(name: str) -> Any:
    mod_name = _EXPORTS.get(name)
    if mod_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    mod = import_module(f'{__name__}.{mod_name}')
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))
