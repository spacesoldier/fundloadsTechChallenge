from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable


@runtime_checkable
class ExecutionObserver(Protocol):
    # Generic execution lifecycle observer used by all runner implementations.
    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> object | None:
        return None

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        outputs: list[object],
        state: object | None,
    ) -> None:
        return None

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        return None

    def on_run_end(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ObserverFactoryMeta:
    # Metadata attached to observer factories for discovery.
    name: str


@dataclass(frozen=True, slots=True)
class ObserverFactoryContext:
    # Runtime context passed to discovered observer factories.
    runtime: dict[str, object]
    adapter_instances: dict[str, object]
    run_id: str
    scenario_id: str
    node_order: list[str]


ObserverFactoryResult = ExecutionObserver | list[ExecutionObserver] | None
ObserverFactory = Callable[[ObserverFactoryContext], ObserverFactoryResult]
T = TypeVar("T")


def observer_factory(*, name: str) -> Callable[[T], T]:
    # Decorator marks a callable as execution observer factory for discovery.
    def _decorate(target: T) -> T:
        setattr(target, "__observer_factory_meta__", ObserverFactoryMeta(name=name))
        return target

    return _decorate


def get_observer_factory_meta(target: object) -> ObserverFactoryMeta | None:
    # Read observer factory metadata if present on callable target.
    meta = getattr(target, "__observer_factory_meta__", None)
    if isinstance(meta, ObserverFactoryMeta):
        return meta
    return None
