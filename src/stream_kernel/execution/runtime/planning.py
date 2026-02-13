from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context.inject import Injected
from stream_kernel.application_context.injection_registry import InjectionRegistry
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
