from __future__ import annotations

from dataclasses import dataclass

import pytest

# Integration between DAG analysis and routing semantics is described in:
# docs/framework/initial_stage/DAG construction.md and Routing semantics.md.
from stream_kernel.kernel.dag import NodeContract, build_dag
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import Router


@dataclass(frozen=True, slots=True)
class X:
    value: str


@dataclass(frozen=True, slots=True)
class Y:
    value: str


@dataclass(frozen=True, slots=True)
class Z:
    value: str


@dataclass(frozen=True, slots=True)
class Batch:
    # Wrapper type to preserve list-as-message semantics (DAG construction §2.2).
    items: list[X]


def test_dag_and_router_multi_output_fanout() -> None:
    # DAG edges are derived from consumes/emits (DAG construction §3).
    contracts = [
        NodeContract(name="source", consumes=[], emits=[X]),
        NodeContract(name="transform", consumes=[X], emits=[Y, Z]),
        NodeContract(name="sink_y", consumes=[Y], emits=[]),
        NodeContract(name="sink_z", consumes=[Z], emits=[]),
    ]

    dag = build_dag(contracts)
    assert dag.edges == [
        ("source", "transform"),
        ("transform", "sink_y"),
        ("transform", "sink_z"),
    ]

    # Router fan-out is type-driven when no explicit target is provided (Routing semantics §5.1).
    router = Router(consumers={Y: ["sink_y"], Z: ["sink_z"]}, strict=True)
    y_payload = Y("y")
    z_payload = Z("z")
    deliveries = router.route([y_payload, z_payload])
    assert deliveries == [("sink_y", y_payload), ("sink_z", z_payload)]


def test_dag_and_router_target_override_with_multi_consumer() -> None:
    # Multiple consumers for the same token create multiple edges in the DAG (§6.1).
    contracts = [
        NodeContract(name="source", consumes=[], emits=[X]),
        NodeContract(name="transform", consumes=[X], emits=[Y]),
        NodeContract(name="sink_a", consumes=[Y], emits=[]),
        NodeContract(name="sink_b", consumes=[Y], emits=[]),
    ]

    dag = build_dag(contracts)
    assert ("transform", "sink_a") in dag.edges
    assert ("transform", "sink_b") in dag.edges

    # Explicit targets must override type fan-out (Routing semantics §5.2).
    router = Router(consumers={Y: ["sink_a", "sink_b"]}, strict=True)
    payload = Y("targeted")
    deliveries = router.route([Envelope(payload=payload, target="sink_b")])
    assert deliveries == [("sink_b", payload)]


def test_router_list_fanout_vs_batch_wrapper() -> None:
    # Returning a list means fan-out; Batch wrapper keeps list as one message (§2.2).
    router = Router(consumers={X: ["sink_x"], Batch: ["sink_batch"]}, strict=True)

    x1 = X("x1")
    x2 = X("x2")
    batch = Batch([x1, x2])

    # Fan-out: list items are routed as separate messages.
    fanout_deliveries = router.route([x1, x2])
    assert fanout_deliveries == [("sink_x", x1), ("sink_x", x2)]

    # List-as-message: a wrapper type routes as a single payload.
    batch_deliveries = router.route([batch])
    assert batch_deliveries == [("sink_batch", batch)]


def test_router_preserves_consumer_order_for_multi_consumer_fanout() -> None:
    # Routing preserves consumer list order for deterministic fan-out (§5.1).
    router = Router(consumers={X: ["B", "C", "A"]}, strict=True)
    payload = X("x")

    deliveries = router.route([payload])
    assert deliveries == [("B", payload), ("C", payload), ("A", payload)]


def test_dag_multi_consumer_fan_in_and_router_fanout() -> None:
    # Fan-in: multiple providers feed one token consumed by multiple sinks (§6.2, §6.1).
    contracts = [
        NodeContract(name="source_a", consumes=[], emits=[X]),
        NodeContract(name="source_b", consumes=[], emits=[X]),
        NodeContract(name="transform", consumes=[X], emits=[Y]),
        NodeContract(name="sink_a", consumes=[Y], emits=[]),
        NodeContract(name="sink_b", consumes=[Y], emits=[]),
    ]

    dag = build_dag(contracts)
    assert ("source_a", "transform") in dag.edges
    assert ("source_b", "transform") in dag.edges
    assert ("transform", "sink_a") in dag.edges
    assert ("transform", "sink_b") in dag.edges

    # Router fan-out: Y should deliver to both sinks in registry order (§5.1).
    router = Router(consumers={Y: ["sink_a", "sink_b"]}, strict=True)
    payload = Y("y")
    deliveries = router.route([payload])
    assert deliveries == [("sink_a", payload), ("sink_b", payload)]


def test_router_target_mismatch_errors_in_strict_mode() -> None:
    # If target exists but does not consume the payload type, strict mode errors (§5.9).
    router = Router(consumers={Y: ["sink_y"]}, strict=True)
    payload = Y("y")
    # "sink_x" exists for X but not for Y: mismatch should raise.
    router_mismatch = Router(consumers={X: ["sink_x"], Y: ["sink_y"]}, strict=True)
    with pytest.raises(ValueError):
        router_mismatch.route([Envelope(payload=payload, target="sink_x")])


def test_router_unknown_target_drops_in_non_strict_mode() -> None:
    # Non-strict mode should drop unknown targets instead of raising (§5.8).
    router = Router(consumers={X: ["sink_x"]}, strict=False)
    payload = X("x")
    deliveries = router.route([Envelope(payload=payload, target="missing")])
    assert deliveries == []


def test_router_no_consumer_errors_in_strict_mode() -> None:
    # No consumers for a payload type is an error in strict mode (§5.1/§5.8).
    router = Router(consumers={}, strict=True)
    with pytest.raises(ValueError):
        router.route([X("x")])


def test_router_no_consumer_drops_in_non_strict_mode() -> None:
    # Non-strict mode should drop payloads with no consumers.
    router = Router(consumers={}, strict=False)
    deliveries = router.route([X("x")])
    assert deliveries == []
