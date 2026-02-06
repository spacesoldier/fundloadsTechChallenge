from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class InjectionRegistryError(RuntimeError):
    # Raised when bindings are missing or duplicated.
    pass


Factory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class _Binding:
    # Internal binding metadata for async-aware planning.
    factory: Factory
    is_async: bool = False


@dataclass(slots=True)
class ScenarioScope:
    # Scenario-scoped instances resolved by (port_type, data_type).
    _instances: dict[tuple[str, type[Any]], object] = field(default_factory=dict)

    def resolve(self, port_type: str, data_type: type[Any]) -> object:
        key = (port_type, data_type)
        if key not in self._instances:
            raise InjectionRegistryError(f"Missing binding for {port_type}<{data_type.__name__}>")
        return self._instances[key]


@dataclass(slots=True)
class InjectionRegistry:
    # Registry of factories keyed by (port_type, data_type).
    _bindings: dict[tuple[str, type[Any]], _Binding] = field(default_factory=dict)

    def register_factory(
        self,
        port_type: str,
        data_type: type[Any],
        factory: Factory,
        *,
        is_async: bool = False,
    ) -> None:
        key = (port_type, data_type)
        if key in self._bindings:
            raise InjectionRegistryError(f"Duplicate binding for {port_type}<{data_type.__name__}>")
        self._bindings[key] = _Binding(factory=factory, is_async=is_async)

    def is_async_binding(self, port_type: str, data_type: type[Any]) -> bool:
        key = (port_type, data_type)
        if key not in self._bindings:
            raise InjectionRegistryError(f"Missing binding for {port_type}<{data_type.__name__}>")
        return self._bindings[key].is_async

    def instantiate_for_scenario(self, scenario_id: str) -> ScenarioScope:
        # Build a fresh set of instances for a scenario.
        instances: dict[tuple[str, type[Any]], object] = {}
        for key, binding in self._bindings.items():
            instances[key] = binding.factory()
        return ScenarioScope(_instances=instances)
