from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.integration.kv_store import validate_kv_contract_type

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Injected:
    # Descriptor-like object representing a dependency to be injected.
    port_type: str
    data_type: type[Any]
    qualifier: str | None = None

    def resolve(self, scope: ScenarioScope) -> object:
        return scope.resolve(self.port_type, self.data_type, qualifier=self.qualifier)


class _InjectFactory:
    # Convenience helpers for standard framework ports.
    def stream(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="stream", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def kv_stream(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="kv_stream", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def kv(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        try:
            validate_kv_contract_type(data_type)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return Injected(port_type="kv", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def request(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="request", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def response(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="response", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def service(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="service", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def queue(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="queue", data_type=data_type, qualifier=_normalize_qualifier(qualifier))

    def topic(self, data_type: type[Any], *, qualifier: str | None = None) -> Injected:
        return Injected(port_type="topic", data_type=data_type, qualifier=_normalize_qualifier(qualifier))


def _normalize_qualifier(qualifier: str | None) -> str | None:
    if qualifier is None:
        return None
    if not isinstance(qualifier, str) or not qualifier:
        raise ValueError("inject qualifier must be a non-empty string when provided")
    return qualifier


inject = _InjectFactory()
