from __future__ import annotations

from dataclasses import dataclass

import pytest

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.application_context.inject import inject, Injected


class EventA:
    pass


@dataclass
class _StreamPort:
    name: str


def test_inject_descriptor_carries_port_and_type() -> None:
    # Injected descriptor should store port and data type metadata.
    dep = inject.stream(EventA)
    assert isinstance(dep, Injected)
    assert dep.port_type == "stream"
    assert dep.data_type is EventA


def test_inject_resolves_from_scope() -> None:
    # Injected descriptor should resolve from scenario scope.
    reg = InjectionRegistry()
    reg.register_factory("stream", EventA, lambda: _StreamPort("A"))
    scope = reg.instantiate_for_scenario("s1")

    dep = inject.stream(EventA)
    resolved = dep.resolve(scope)
    assert isinstance(resolved, _StreamPort)
    assert resolved.name == "A"


def test_inject_missing_binding_raises() -> None:
    # Missing binding should raise from resolve().
    reg = InjectionRegistry()
    scope = reg.instantiate_for_scenario("s1")
    dep = inject.stream(EventA)

    with pytest.raises(Exception):
        dep.resolve(scope)
