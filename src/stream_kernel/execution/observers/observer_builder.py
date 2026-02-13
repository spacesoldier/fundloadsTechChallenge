from __future__ import annotations

from types import ModuleType

from stream_kernel.execution.observers.observer import (
    ExecutionObserver,
    ObserverFactory,
    ObserverFactoryContext,
)
from stream_kernel.execution.observers.observer_discovery import discover_execution_observer_factories


def build_execution_observers(
    *,
    modules: list[ModuleType],
    runtime: dict[str, object],
    adapter_instances: dict[str, object],
    run_id: str,
    scenario_id: str,
    node_order: list[str],
) -> list[ExecutionObserver]:
    # Public execution-level API: discover and build observers from modules.
    factories = discover_execution_observer_factories(modules)
    return build_execution_observers_from_factories(
        factories=factories,
        runtime=runtime,
        adapter_instances=adapter_instances,
        run_id=run_id,
        scenario_id=scenario_id,
        node_order=node_order,
    )


def build_execution_observers_from_factories(
    *,
    factories: dict[str, ObserverFactory],
    runtime: dict[str, object],
    adapter_instances: dict[str, object],
    run_id: str,
    scenario_id: str,
    node_order: list[str],
) -> list[ExecutionObserver]:
    # Build execution observers from discovered observer factories.
    context = ObserverFactoryContext(
        runtime=runtime,
        adapter_instances=adapter_instances,
        run_id=run_id,
        scenario_id=scenario_id,
        node_order=node_order,
    )
    observers: list[ExecutionObserver] = []
    for _name, factory in factories.items():
        built = factory(context)
        candidates: list[object]
        if built is None:
            continue
        if isinstance(built, list):
            candidates = list(built)
        else:
            candidates = [built]
        for candidate in candidates:
            if not isinstance(candidate, ExecutionObserver):
                raise ValueError("Observer factory must return ExecutionObserver instances")
            observers.append(candidate)
    return observers
