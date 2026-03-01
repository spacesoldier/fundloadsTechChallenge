from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    'ControlPlaneLeafBootstrapNode': 'system_nodes',
    'ControlPlaneLeafApplyConfigNode': 'system_nodes',
    'ControlPlaneLeafDiscoveryRequestNode': 'system_nodes',
    'ControlPlaneLeafSnapshotApplyNode': 'system_nodes',
    'ControlPlaneLeafConfigApplyRuntimeNode': 'system_nodes',
    'ControlPlaneLeafBoundaryExecuteNode': 'system_nodes',
    'ControlPlaneLeafStopNode': 'system_nodes',
}


def __getattr__(name: str) -> Any:
    mod_name = _EXPORTS.get(name)
    if mod_name is None:
        raise AttributeError(name)
    mod = import_module(f'{__name__}.{mod_name}')
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))
