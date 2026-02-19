from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass

from stream_kernel.application_context.inject import Injected
from stream_kernel.application_context.injection_registry import InjectionRegistry, InjectionRegistryError
from stream_kernel.kernel.dag import Dag


@dataclass(frozen=True, slots=True)
class PoolPlan:
    # Mapping of node names to execution pools (Execution planning model §8).
    pools: dict[str, str]


def plan_pools(nodes: dict[str, object], registry: InjectionRegistry) -> dict[str, str]:
    # Determine sync/async pools based on injected async-capable dependencies.
    plan: dict[str, str] = {}
    for name, node in nodes.items():
        injected = _iter_injected(node)
        is_async = any(_is_async_dependency(registry, dep) for dep in injected)
        plan[name] = "async" if is_async else "sync"
    return plan


def _iter_injected(obj: object) -> list[Injected]:
    # Collect @inject fields from both instance and class (Injection model §3.3).
    injected: list[Injected] = []

    def _collect(target: object) -> None:
        if is_dataclass(target):
            for dataclass_field in fields(target):
                try:
                    value = getattr(target, dataclass_field.name)
                except AttributeError:
                    continue
                if isinstance(value, Injected):
                    injected.append(value)
        for value in getattr(target, "__dict__", {}).values():
            if isinstance(value, Injected):
                injected.append(value)
        target_cls = getattr(target, "__class__", None)
        if target_cls is not None:
            for value in getattr(target_cls, "__dict__", {}).values():
                if isinstance(value, Injected):
                    injected.append(value)

    _collect(obj)
    container = getattr(obj, "__self__", None)
    if container is not None:
        _collect(container)

    deduped: dict[tuple[str, type[object], str | None], Injected] = {}
    for marker in injected:
        key = (marker.port_type, marker.data_type, marker.qualifier)
        deduped[key] = marker
    return list(deduped.values())


def _is_async_dependency(registry: InjectionRegistry, dependency: Injected) -> bool:
    try:
        return registry.is_async_binding(
            dependency.port_type,
            dependency.data_type,
            qualifier=dependency.qualifier,
        )
    except InjectionRegistryError:
        # Planning fallback: unresolved bindings are treated as sync here;
        # strict DI validation still fails during scenario injection.
        return False


def build_execution_plan(dag: Dag) -> list[str]:
    # Build deterministic topological order from DAG edges.
    # Tie-break between ready nodes follows dag.nodes declaration order.
    node_order = {name: idx for idx, name in enumerate(dag.nodes)}
    adjacency: dict[str, list[str]] = {name: [] for name in dag.nodes}
    indegree: dict[str, int] = {name: 0 for name in dag.nodes}

    for src, dst in dag.edges:
        if src not in adjacency:
            adjacency[src] = []
            indegree[src] = indegree.get(src, 0)
            node_order.setdefault(src, len(node_order))
        if dst not in adjacency:
            adjacency[dst] = []
            indegree[dst] = indegree.get(dst, 0)
            node_order.setdefault(dst, len(node_order))
        adjacency[src].append(dst)
        indegree[dst] = indegree.get(dst, 0) + 1

    ready = sorted((name for name, deg in indegree.items() if deg == 0), key=node_order.get)
    plan: list[str] = []

    while ready:
        current = ready.pop(0)
        plan.append(current)
        for nxt in adjacency.get(current, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=node_order.get)

    if len(plan) != len(indegree):
        raise ValueError("DAG execution plan cannot be built: cycle detected")
    return plan
