from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ServiceMeta:
    # Metadata marker for service components managed by framework DI.
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ServiceMeta.name must be a non-empty string")


def service(*, name: str | None = None) -> Callable[[T], T]:
    # Explicit marker for service declarations.
    def _decorate(target: T) -> T:
        resolved_name = name if name is not None else getattr(target, "__name__", "")
        meta = ServiceMeta(name=resolved_name)
        setattr(target, "__service_meta__", meta)
        return target

    return _decorate


def discover_services(modules: list[ModuleType]) -> list[type[object]]:
    # Discover service classes marked with @service in provided modules.
    discovered: list[type[object]] = []
    seen_names: set[str] = set()
    seen_targets: dict[str, type[object]] = {}
    for module in modules:
        for value in module.__dict__.values():
            meta = getattr(value, "__service_meta__", None)
            if not isinstance(meta, ServiceMeta):
                continue
            if not isinstance(value, type):
                raise ValueError(f"Service '{meta.name}' target is not a class")
            if meta.name in seen_names:
                if seen_targets.get(meta.name) is value:
                    # Same class re-exported through multiple modules is not a conflict.
                    continue
                raise ValueError(f"Duplicate service name discovered: {meta.name}")
            seen_names.add(meta.name)
            seen_targets[meta.name] = value
            discovered.append(value)
    return discovered


def service_contract_types(service_cls: type[object]) -> list[type[object]]:
    # Register service by concrete class and public base contracts for flexible injection.
    contracts: list[type[object]] = [service_cls]
    for base in service_cls.__mro__[1:]:
        if base is object:
            continue
        contracts.append(base)
    return contracts
