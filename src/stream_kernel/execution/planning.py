from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import Injected
from stream_kernel.application_context.injection_registry import InjectionRegistry


@dataclass(frozen=True, slots=True)
class PoolPlan:
    # Mapping of node names to execution pools (Execution planning model §8).
    pools: dict[str, str]


def plan_pools(nodes: dict[str, object], registry: InjectionRegistry) -> dict[str, str]:
    # Determine sync/async pools based on injected async-capable dependencies.
    plan: dict[str, str] = {}
    for name, node in nodes.items():
        injected = _iter_injected(node)
        is_async = any(
            registry.is_async_binding(dep.port_type, dep.data_type) for dep in injected
        )
        plan[name] = "async" if is_async else "sync"
    return plan


def _iter_injected(obj: object) -> list[Injected]:
    # Collect @inject fields from both instance and class (Injection model §3.3).
    injected: list[Injected] = []
    for value in vars(obj).values():
        if isinstance(value, Injected):
            injected.append(value)
    for value in vars(obj.__class__).values():
        if isinstance(value, Injected):
            injected.append(value)
    return injected
