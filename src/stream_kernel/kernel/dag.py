from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


class DagError(ValueError):
    # Base error for DAG construction and validation failures.
    pass


class MissingProviderError(DagError):
    # Raised when a consumed type has no providers.
    pass


class CycleError(DagError):
    # Raised when the node graph contains a directed cycle.
    pass


@dataclass(frozen=True, slots=True)
class NodeContract:
    # Minimal contract used by the DAG builder (name + consumes/emits).
    name: str
    consumes: list[type] = field(default_factory=list)
    emits: list[type] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Dag:
    # DAG representation for analysis: nodes + directed edges.
    nodes: list[str]
    edges: list[tuple[str, str]]


def build_dag(contracts: Sequence[NodeContract]) -> Dag:
    # Build a DAG from consumes/emits contracts (docs/framework/initial_stage/DAG construction.md).
    if not contracts:
        return Dag(nodes=[], edges=[])

    # Enforce non-empty consumes for non-source nodes.
    for contract in contracts:
        if not contract.consumes:
            if contract.emits:
                continue
            raise DagError(f"Node '{contract.name}' must declare non-empty consumes")

    # Providers and consumers indexed by token (type).
    providers: dict[type, list[str]] = {}
    consumers: dict[type, list[str]] = {}

    for contract in contracts:
        for token in contract.emits:
            providers.setdefault(token, []).append(contract.name)
        for token in contract.consumes:
            consumers.setdefault(token, []).append(contract.name)

    # Verify every consumed token has at least one provider.
    for token, consumer_names in consumers.items():
        if token not in providers:
            raise MissingProviderError(f"Token '{token.__name__}' has no providers for consumers {consumer_names}")

    # Build edges in deterministic order (discovery order).
    edge_set: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []

    for token, consumer_names in consumers.items():
        for provider in providers.get(token, []):
            for consumer in consumer_names:
                edge = (provider, consumer)
                if edge in edge_set:
                    continue
                edge_set.add(edge)
                edges.append(edge)

    # Self-loops are explicit cycles.
    if any(src == dst for src, dst in edges):
        raise CycleError("Self-loop detected in DAG")

    _assert_acyclic([c.name for c in contracts], edges)
    return Dag(nodes=[c.name for c in contracts], edges=edges)


def _assert_acyclic(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> None:
    # Depth-first cycle detection on the node graph.
    adjacency: dict[str, list[str]] = {name: [] for name in nodes}
    for src, dst in edges:
        adjacency.setdefault(src, []).append(dst)
        adjacency.setdefault(dst, [])

    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node: str) -> None:
        if node in visiting:
            raise CycleError(f"Cycle detected at node '{node}'")
        if node in visited:
            return
        visiting.add(node)
        for nxt in adjacency.get(node, []):
            _visit(nxt)
        visiting.remove(node)
        visited.add(node)

    for node in list(adjacency.keys()):
        if node not in visited:
            _visit(node)
