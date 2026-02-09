from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, is_dataclass
from dataclasses import dataclass, field
from typing import Any

from stream_kernel.integration.kv_store import KVStore, validate_kv_contract_type


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
    _instances: dict[tuple[str, type[Any], str | None], object] = field(default_factory=dict)

    def resolve(self, port_type: str, data_type: type[Any], *, qualifier: str | None = None) -> object:
        normalized_qualifier = _normalize_qualifier(qualifier)
        key = (port_type, data_type, normalized_qualifier)
        if key not in self._instances:
            raise InjectionRegistryError(_missing_binding_message(port_type, data_type, normalized_qualifier))
        return self._instances[key]


@dataclass(slots=True)
class InjectionRegistry:
    # Registry of factories keyed by (port_type, data_type).
    _bindings: dict[tuple[str, type[Any], str | None], _Binding] = field(default_factory=dict)

    def register_factory(
        self,
        port_type: str,
        data_type: type[Any],
        factory: Factory,
        *,
        is_async: bool = False,
        qualifier: str | None = None,
    ) -> None:
        if port_type == "kv":
            try:
                validate_kv_contract_type(data_type)
            except TypeError as exc:
                raise InjectionRegistryError(str(exc)) from exc
        normalized_qualifier = _normalize_qualifier(qualifier)
        key = (port_type, data_type, normalized_qualifier)
        if key in self._bindings:
            raise InjectionRegistryError(_duplicate_binding_message(port_type, data_type, normalized_qualifier))
        self._bindings[key] = _Binding(factory=factory, is_async=is_async)

    def is_async_binding(
        self,
        port_type: str,
        data_type: type[Any],
        *,
        qualifier: str | None = None,
    ) -> bool:
        normalized_qualifier = _normalize_qualifier(qualifier)
        key = (port_type, data_type, normalized_qualifier)
        if key not in self._bindings:
            raise InjectionRegistryError(_missing_binding_message(port_type, data_type, normalized_qualifier))
        return self._bindings[key].is_async

    def instantiate_for_scenario(self, scenario_id: str) -> ScenarioScope:
        # Build a fresh set of instances for a scenario.
        instances: dict[tuple[str, type[Any], str | None], object] = {}
        for key, binding in self._bindings.items():
            instances[key] = binding.factory()
        _materialize_kv_marker_instances(instances, self._bindings)
        scope = ScenarioScope(_instances=instances)
        # Support framework-style DI inside service objects themselves.
        for instance in instances.values():
            _apply_scope_injection(instance, scope)
        return scope


def _is_injected_marker(value: object) -> bool:
    return (
        hasattr(value, "port_type")
        and hasattr(value, "data_type")
        and callable(getattr(value, "resolve", None))
    )


def _iter_injected_fields(obj: object):
    if is_dataclass(obj):
        for f in fields(obj):
            value = getattr(obj, f.name)
            if _is_injected_marker(value):
                yield f.name, value
        return

    for name, value in getattr(obj, "__dict__", {}).items():
        if _is_injected_marker(value):
            yield name, value
    for name, value in getattr(obj.__class__, "__dict__", {}).items():
        if _is_injected_marker(value):
            yield name, value


def _apply_scope_injection(obj: object, scope: ScenarioScope) -> None:
    for name, injected in _iter_injected_fields(obj):
        resolved = injected.resolve(scope)
        object.__setattr__(obj, name, resolved)


def _materialize_kv_marker_instances(
    instances: dict[tuple[str, type[Any], str | None], object],
    bindings: dict[tuple[str, type[Any], str | None], _Binding],
) -> None:
    # If only kv<KVStore> is bound, create per-marker KV instances on demand for marker contracts.
    required_markers: set[tuple[type[Any], str | None]] = set()
    for instance in instances.values():
        for _name, injected in _iter_injected_fields(instance):
            if getattr(injected, "port_type", None) != "kv":
                continue
            data_type = getattr(injected, "data_type", None)
            qualifier = _normalize_qualifier(getattr(injected, "qualifier", None))
            if not isinstance(data_type, type):
                continue
            if not issubclass(data_type, KVStore):
                continue
            required_markers.add((data_type, qualifier))

    for marker_type, qualifier in required_markers:
        key = ("kv", marker_type, qualifier)
        if key in instances:
            continue
        base_binding = _resolve_kv_base_binding(bindings, qualifier)
        if base_binding is None:
            continue
        instances[key] = base_binding.factory()


def _resolve_kv_base_binding(
    bindings: dict[tuple[str, type[Any], str | None], _Binding],
    qualifier: str | None,
) -> _Binding | None:
    # Priority: exact kv<KVStore, qualifier> then unqualified kv<KVStore>.
    exact = bindings.get(("kv", KVStore, qualifier))
    if exact is not None:
        return exact
    if qualifier is not None:
        return bindings.get(("kv", KVStore, None))
    return None


def _normalize_qualifier(qualifier: str | None) -> str | None:
    if qualifier is None:
        return None
    if not isinstance(qualifier, str) or not qualifier:
        raise InjectionRegistryError("binding qualifier must be a non-empty string when provided")
    return qualifier


def _missing_binding_message(port_type: str, data_type: type[Any], qualifier: str | None) -> str:
    suffix = f"#{qualifier}" if qualifier is not None else ""
    return f"Missing binding for {port_type}<{data_type.__name__}>{suffix}"


def _duplicate_binding_message(port_type: str, data_type: type[Any], qualifier: str | None) -> str:
    suffix = f"#{qualifier}" if qualifier is not None else ""
    return f"Duplicate binding for {port_type}<{data_type.__name__}>{suffix}"
