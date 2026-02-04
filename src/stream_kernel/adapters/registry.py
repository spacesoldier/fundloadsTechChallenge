from __future__ import annotations

from typing import Callable


class AdapterRegistryError(ValueError):
    # Raised when adapter lookup/build fails.
    pass


class AdapterRegistry:
    # Registry of adapter factories keyed by role + kind.
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], Callable[[dict[str, object]], object]] = {}

    def register(self, role: str, kind: str, factory: Callable[[dict[str, object]], object]) -> None:
        key = (role, kind)
        if key in self._factories:
            raise AdapterRegistryError(f"Duplicate adapter registration: {role}/{kind}")
        self._factories[key] = factory

    def build(self, role: str, config: dict[str, object]) -> object:
        if not isinstance(config, dict):
            raise AdapterRegistryError("Adapter config must be a mapping")
        kind = config.get("kind")
        if not isinstance(kind, str):
            raise AdapterRegistryError("Adapter kind must be a string")
        settings = config.get("settings", {})
        if not isinstance(settings, dict):
            raise AdapterRegistryError("Adapter settings must be a mapping")
        key = (role, kind)
        if key not in self._factories:
            raise AdapterRegistryError(f"Unknown adapter kind for role {role}: {kind}")
        return self._factories[key](settings)
