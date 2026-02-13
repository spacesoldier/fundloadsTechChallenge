from __future__ import annotations

from types import ModuleType

import pytest

from stream_kernel.execution.observers.observer import observer_factory
from stream_kernel.execution.observers.observer_builder import (
    build_execution_observers,
    build_execution_observers_from_factories,
)


class _Observer:
    def before_node(self, **kwargs: object) -> object | None:
        return None

    def after_node(self, **kwargs: object) -> None:
        return None

    def on_node_error(self, **kwargs: object) -> None:
        return None

    def on_run_end(self) -> None:
        return None


def test_build_execution_observers_discovers_and_builds_from_modules() -> None:
    # Public builder should discover decorated factories and instantiate observers.
    module = ModuleType("fake.observers")

    @observer_factory(name="obs")
    def _factory(_ctx):
        return _Observer()

    module.factory = _factory

    observers = build_execution_observers(
        modules=[module],
        runtime={},
        adapter_instances={},
        run_id="run",
        scenario_id="scenario",
        node_order=[],
    )
    assert len(observers) == 1


def test_build_execution_observers_from_factories_rejects_invalid_result() -> None:
    # Builder must fail fast when factory result is not an observer.
    with pytest.raises(ValueError):
        build_execution_observers_from_factories(
            factories={"bad": lambda _ctx: object()},
            runtime={},
            adapter_instances={},
            run_id="run",
            scenario_id="scenario",
            node_order=[],
        )
