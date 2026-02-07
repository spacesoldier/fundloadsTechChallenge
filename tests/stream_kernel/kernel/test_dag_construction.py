from __future__ import annotations

import pytest

# DAG analysis rules are defined in docs/framework/initial_stage/DAG construction.md.
from stream_kernel.kernel.dag import (
    DagError,
    MissingProviderError,
    CycleError,
    NodeContract,
    build_dag,
)


class X:
    pass


class Y:
    pass


class Z:
    pass


class W:
    pass


def test_dag_fanout_edges_are_created() -> None:
    # Fan-out: one provider to many consumers (DAG construction §6.1).
    nodes = [
        NodeContract(name="Source", consumes=[], emits=[Y]),
        NodeContract(name="A", consumes=[Y], emits=[X]),
        NodeContract(name="B", consumes=[X], emits=[]),
        NodeContract(name="C", consumes=[X], emits=[]),
    ]
    dag = build_dag(nodes)
    assert dag.edges == [("Source", "A"), ("A", "B"), ("A", "C")]


def test_dag_fanin_edges_are_created() -> None:
    # Fan-in: many providers to one consumer (DAG construction §6.2).
    nodes = [
        NodeContract(name="Source", consumes=[], emits=[Y]),
        NodeContract(name="A", consumes=[Y], emits=[X]),
        NodeContract(name="B", consumes=[Y], emits=[X]),
        NodeContract(name="C", consumes=[X], emits=[]),
    ]
    dag = build_dag(nodes)
    assert dag.edges == [("Source", "A"), ("Source", "B"), ("A", "C"), ("B", "C")]


def test_dag_multiple_consumes_collapses_duplicate_edges() -> None:
    # Multiple consumes on the same node should not duplicate edges (DAG construction §6.2.1).
    nodes = [
        NodeContract(name="Source", consumes=[], emits=[Z]),
        NodeContract(name="A", consumes=[Z], emits=[X, Y]),
        NodeContract(name="B", consumes=[X, Y], emits=[]),
    ]
    dag = build_dag(nodes)
    assert dag.edges == [("Source", "A"), ("A", "B")]


def test_dag_missing_provider_fails() -> None:
    # Missing provider for a consumed type must fail (DAG construction §6.3).
    nodes = [
        NodeContract(name="A", consumes=[X], emits=[]),
    ]
    with pytest.raises(MissingProviderError):
        build_dag(nodes)


def test_dag_self_cycle_fails() -> None:
    # Self-loop is a cycle (DAG construction §6.4.1).
    nodes = [
        NodeContract(name="A", consumes=[X], emits=[X]),
    ]
    with pytest.raises(CycleError):
        build_dag(nodes)


def test_dag_figure_eight_cycle_fails() -> None:
    # Overlapping cycles must be detected (DAG construction §6.4.2).
    nodes = [
        NodeContract(name="A", consumes=[Y], emits=[X]),
        NodeContract(name="B", consumes=[X], emits=[Y, Z]),
        NodeContract(name="C", consumes=[Z], emits=[W]),
        NodeContract(name="D", consumes=[W], emits=[Y]),
    ]
    with pytest.raises(CycleError):
        build_dag(nodes)


def test_dag_fan_in_out_cycle_fails() -> None:
    # Larger cycle with fan-in/fan-out (DAG construction §6.4.3).
    nodes = [
        NodeContract(name="A", consumes=[Y], emits=[X]),
        NodeContract(name="B", consumes=[X], emits=[Y]),
        NodeContract(name="C", consumes=[Z], emits=[X]),
        NodeContract(name="D", consumes=[X], emits=[Z]),
        NodeContract(name="E", consumes=[Z], emits=[W]),
        NodeContract(name="F", consumes=[W], emits=[Y]),
    ]
    with pytest.raises(CycleError):
        build_dag(nodes)


def test_dag_requires_non_empty_consumes() -> None:
    # consumes must be non-empty for non-source nodes (DAG construction §6.6).
    nodes = [
        NodeContract(name="A", consumes=[], emits=[]),
    ]
    with pytest.raises(DagError):
        build_dag(nodes)


def test_dag_allows_source_nodes_with_empty_consumes() -> None:
    # Source nodes can emit without consuming; edges should connect to consumers.
    nodes = [
        NodeContract(name="Source", consumes=[], emits=[X]),
        NodeContract(name="A", consumes=[X], emits=[]),
    ]
    dag = build_dag(nodes)
    assert dag.edges == [("Source", "A")]


def test_dag_empty_contracts_returns_empty_graph() -> None:
    # Empty input yields an empty DAG (defensive behavior).
    dag = build_dag([])
    assert dag.nodes == []
    assert dag.edges == []
