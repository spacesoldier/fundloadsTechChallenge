from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    'ControlPlaneLifecycleOrchestrationService': 'startup.lifecycle_service',
    'DefaultControlPlaneLifecycleOrchestrationService': 'startup.lifecycle_service',
    'ControlPlaneSpawnDispatchNode': 'startup.system_nodes',
    'ControlPlaneGroupStartupWaitNode': 'startup.system_nodes',
    'LifecycleSystemPlan': 'startup.planning',
    'build_lifecycle_system_plan': 'startup.planning',
    'ControlPlaneRootRuntimeLifecycleManager': 'runtime.lifecycle_manager',
}


def __getattr__(name: str) -> Any:
    mod_name = _EXPORTS.get(name)
    if mod_name is None:
        raise AttributeError(name)
    mod = import_module(f'{__name__}.{mod_name}')
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))
