from __future__ import annotations

from types import ModuleType

from stream_kernel.execution.observers.observer import (
    ObserverFactory,
    get_observer_factory_meta,
)


class ObserverDiscoveryError(RuntimeError):
    # Raised when observer factory discovery finds duplicate names.
    pass


def discover_execution_observer_factories(
    modules: list[ModuleType],
) -> dict[str, ObserverFactory]:
    # Discover execution observer factories declared via @observer_factory(name=...).
    discovered: dict[str, ObserverFactory] = {}
    for module in modules:
        for value in module.__dict__.values():
            meta = get_observer_factory_meta(value)
            if meta is None:
                continue
            if not callable(value):
                raise ObserverDiscoveryError(f"Observer factory '{meta.name}' is not callable")
            if meta.name in discovered:
                raise ObserverDiscoveryError(f"Duplicate observer factory discovered: {meta.name}")
            discovered[meta.name] = value
    return discovered
