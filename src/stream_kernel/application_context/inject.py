from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from stream_kernel.application_context.injection_registry import ScenarioScope

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Injected:
    # Descriptor-like object representing a dependency to be injected.
    port_type: str
    data_type: type[Any]

    def resolve(self, scope: ScenarioScope) -> object:
        return scope.resolve(self.port_type, self.data_type)


class _InjectFactory:
    # Convenience helpers: inject.stream(Type) and inject.kv(Type).
    def stream(self, data_type: type[Any]) -> Injected:
        return Injected(port_type="stream", data_type=data_type)

    def kv(self, data_type: type[Any]) -> Injected:
        return Injected(port_type="kv", data_type=data_type)


inject = _InjectFactory()
