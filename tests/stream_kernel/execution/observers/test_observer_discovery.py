from __future__ import annotations

from types import ModuleType

import pytest

from stream_kernel.execution.observers.observer import observer_factory
from stream_kernel.execution.observers.observer_discovery import (
    ObserverDiscoveryError,
    discover_execution_observer_factories,
)


def test_discover_execution_observer_factories_collects_decorated_callables() -> None:
    module = ModuleType("fake.observers")

    @observer_factory(name="a")
    def _a(_ctx):  # type: ignore[no-untyped-def]
        return None

    @observer_factory(name="b")
    def _b(_ctx):  # type: ignore[no-untyped-def]
        return None

    module.a = _a
    module.b = _b
    discovered = discover_execution_observer_factories([module])
    assert set(discovered.keys()) == {"a", "b"}


def test_discover_execution_observer_factories_rejects_duplicates() -> None:
    module = ModuleType("fake.observers")

    @observer_factory(name="dup")
    def _a(_ctx):  # type: ignore[no-untyped-def]
        return None

    @observer_factory(name="dup")
    def _b(_ctx):  # type: ignore[no-untyped-def]
        return None

    module.a = _a
    module.b = _b
    with pytest.raises(ObserverDiscoveryError):
        discover_execution_observer_factories([module])
