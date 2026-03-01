from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    'ControlPlaneLifecycleOrchestrationService': 'lifecycle_service',
    'DefaultControlPlaneLifecycleOrchestrationService': 'lifecycle_service',
    'RootLifecycleLogFactory': 'log_factory_service',
    'DefaultRootLifecycleLogFactory': 'log_factory_service',
    'RootConsoleLogDispatchService': 'console_log_dispatch_service',
    'DefaultRootConsoleLogDispatchService': 'console_log_dispatch_service',
    'ControlPlaneSpawnDispatchNode': 'system_nodes',
    'ControlPlaneGroupStartupWaitNode': 'system_nodes',
    'ControlPlaneLifecycleLogDispatchNode': 'system_nodes',
    'LifecycleSystemPlan': 'planning',
    'build_lifecycle_system_plan': 'planning',
}


def __getattr__(name: str) -> Any:
    mod_name = _EXPORTS.get(name)
    if mod_name is None:
        raise AttributeError(name)
    mod = import_module(f'{__name__}.{mod_name}')
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))
