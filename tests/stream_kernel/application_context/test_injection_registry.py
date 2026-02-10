from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)
from stream_kernel.application_context.inject import inject
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


@dataclass
class _StreamPort:
    name: str


@dataclass
class _StorePort:
    name: str


class EventA:
    pass


class EventB:
    pass


class _StateKVStore(KVStore):
    # Marker KV contract for state storage.
    pass


class _BadStateKVStore(KVStore):
    # Invalid marker: extends KV API.
    def keys(self) -> list[str]:
        return []


class _PrimeKVStore(KVStore):
    # Marker contract for prime-check cache storage.
    pass


def test_registry_resolves_by_port_and_type() -> None:
    # Resolution should use (port_type, data_type) keys.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"))

    scope = reg.instantiate_for_scenario("s1")
    assert scope.resolve("stream", EventA).name == "A"
    assert scope.resolve("stream", EventB).name == "B"


def test_registry_resolves_by_port_type_and_qualifier() -> None:
    # Qualifier disambiguates bindings for the same port/data contract.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("primary"), qualifier="primary")
    reg.register_factory("stream", EventA, lambda: _StreamPort("default"))

    scope = reg.instantiate_for_scenario("s1")
    assert scope.resolve("stream", EventA, qualifier="primary").name == "primary"
    assert scope.resolve("stream", EventA).name == "default"


def test_registry_creates_new_instances_per_scenario() -> None:
    # Each scenario should receive distinct instances.
    reg = InjectionRegistry()
    reg.register_factory("kv", _StateKVStore, lambda: InMemoryKvStore())

    scope1 = reg.instantiate_for_scenario("s1")
    scope2 = reg.instantiate_for_scenario("s2")

    assert scope1.resolve("kv", _StateKVStore) is not scope2.resolve("kv", _StateKVStore)


def test_registry_errors_on_missing_binding() -> None:
    # Missing bindings must raise an error.
    reg = InjectionRegistry()
    scope = reg.instantiate_for_scenario("s1")
    with pytest.raises(InjectionRegistryError):
        scope.resolve("stream", EventA)


def test_registry_errors_on_duplicate_binding() -> None:
    # Duplicate bindings must raise an error.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    with pytest.raises(InjectionRegistryError):
        reg.register_factory("stream", EventA, lambda: _StreamPort("B"))


def test_registry_rejects_empty_qualifier() -> None:
    # Qualifier must be a non-empty string for unambiguous keying.
    reg = InjectionRegistry()
    with pytest.raises(InjectionRegistryError):
        reg.register_factory("stream", EventA, lambda: _StreamPort("A"), qualifier="")


def test_registry_tracks_async_capability() -> None:
    # Registry should expose async capability for planning (Execution planning §8).
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"), is_async=True)
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"), is_async=False)
    assert reg.is_async_binding("stream", EventA) is True
    assert reg.is_async_binding("stream", EventB) is False


def test_registry_tracks_async_capability_with_qualifier() -> None:
    # Async planning must consider qualifier-specific bindings.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("primary"), is_async=True, qualifier="primary")
    reg.register_factory("stream", EventA, lambda: _StreamPort("default"), is_async=False)
    assert reg.is_async_binding("stream", EventA, qualifier="primary") is True
    assert reg.is_async_binding("stream", EventA) is False


def test_registry_rejects_extended_kv_contract() -> None:
    # KV bindings must use base or marker contracts without extra public methods.
    reg = InjectionRegistry()
    with pytest.raises(InjectionRegistryError):
        reg.register_factory("kv", _BadStateKVStore, lambda: InMemoryKvStore())


def test_registry_autowires_kv_markers_from_base_kv_binding() -> None:
    # If only kv<KVStore> is bound, marker contracts should still resolve with dedicated instances.
    reg = InjectionRegistry()
    reg.register_factory("kv", KVStore, lambda: InMemoryKvStore())

    @dataclass
    class _Service:
        state_store: object = inject.kv(_StateKVStore)  # type: ignore[assignment]
        prime_store: object = inject.kv(_PrimeKVStore)  # type: ignore[assignment]

    reg.register_factory("service", _Service, lambda: _Service())
    scope = reg.instantiate_for_scenario("s1")
    service = scope.resolve("service", _Service)
    assert isinstance(service.state_store, InMemoryKvStore)
    assert isinstance(service.prime_store, InMemoryKvStore)
    assert service.state_store is not service.prime_store


def test_registry_autowires_kv_markers_with_qualifier_specific_base() -> None:
    # Marker injections with qualifier should use matching qualified KV base binding first.
    reg = InjectionRegistry()
    primary = InMemoryKvStore()
    fallback = InMemoryKvStore()
    reg.register_factory("kv", KVStore, lambda _p=fallback: _p)
    reg.register_factory("kv", KVStore, lambda _p=primary: _p, qualifier="state")

    @dataclass
    class _Service:
        state_store: object = inject.kv(_StateKVStore, qualifier="state")  # type: ignore[assignment]
        default_store: object = inject.kv(_PrimeKVStore)  # type: ignore[assignment]

    reg.register_factory("service", _Service, lambda: _Service())
    scope = reg.instantiate_for_scenario("s1")
    service = scope.resolve("service", _Service)
    assert service.state_store is primary
    assert service.default_store is fallback


def test_scenario_scope_close_calls_close_once_per_instance() -> None:
    # Scope shutdown should finalize each unique instance once via close().
    closed: list[str] = []

    class _Closable:
        def close(self) -> None:
            closed.append("x")

    instance = _Closable()
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda _i=instance: _i)
    reg.register_factory("stream", EventB, lambda _i=instance: _i)

    scope = reg.instantiate_for_scenario("s1")
    scope.close()
    scope.close()
    assert closed == ["x"]


def test_scenario_scope_close_falls_back_to_shutdown() -> None:
    # If close() is absent, scope should call shutdown().
    shutdown_calls: list[str] = []

    class _ShutdownOnly:
        def shutdown(self) -> None:
            shutdown_calls.append("x")

    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _ShutdownOnly())
    scope = reg.instantiate_for_scenario("s1")
    scope.close()
    assert shutdown_calls == ["x"]


def test_scenario_scope_resolve_after_close_fails() -> None:
    # Closed scopes should reject further resolution to prevent lifecycle misuse.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    scope = reg.instantiate_for_scenario("s1")
    scope.close()
    with pytest.raises(InjectionRegistryError, match="ScenarioScope is closed"):
        scope.resolve("stream", EventA)
