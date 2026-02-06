from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)


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


def test_registry_resolves_by_port_and_type() -> None:
    # Resolution should use (port_type, data_type) keys.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"))

    scope = reg.instantiate_for_scenario("s1")
    assert scope.resolve("stream", EventA).name == "A"
    assert scope.resolve("stream", EventB).name == "B"


def test_registry_creates_new_instances_per_scenario() -> None:
    # Each scenario should receive distinct instances.
    reg = InjectionRegistry()
    reg.register_factory("kv", EventA, lambda: _StorePort("X"))

    scope1 = reg.instantiate_for_scenario("s1")
    scope2 = reg.instantiate_for_scenario("s2")

    assert scope1.resolve("kv", EventA) is not scope2.resolve("kv", EventA)


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


def test_registry_tracks_async_capability() -> None:
    # Registry should expose async capability for planning (Execution planning §8).
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"), is_async=True)
    reg.register_factory("stream", EventB, lambda: _StreamPort("B"), is_async=False)
    assert reg.is_async_binding("stream", EventA) is True
    assert reg.is_async_binding("stream", EventB) is False
