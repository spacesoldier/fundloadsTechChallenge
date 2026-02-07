from __future__ import annotations

from dataclasses import dataclass

import pytest

# Runner+router integration rules are defined in:
# docs/framework/initial_stage/Execution runtime and routing integration.md
# docs/framework/initial_stage/Routing semantics.md
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.context_store import InMemoryContextStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.routing.envelope import Envelope


@dataclass(frozen=True, slots=True)
class X:
    value: str


@dataclass(frozen=True, slots=True)
class Y:
    value: str


def test_runner_routes_outputs_via_routing_port() -> None:
    # Runner should route node outputs through RoutingPort (Execution runtime §6).
    seen: list[tuple[str, object]] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [X("x")]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(("B", payload))
        return []

    def node_c(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(("C", payload))
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["B", "C"])
    routing = RoutingPort(registry=registry, strict=True)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b, "C": node_c},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    assert seen == [("B", X("x")), ("C", X("x"))]


def test_runner_respects_targeted_envelope_outputs() -> None:
    # Targeted envelopes should override fan-out (Routing semantics §5.2).
    seen: list[str] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [Envelope(payload=X("x"), target="C")]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append("B")
        return []

    def node_c(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append("C")
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["B", "C"])
    routing = RoutingPort(registry=registry, strict=True)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b, "C": node_c},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    assert seen == ["C"]


def test_runner_raises_on_no_consumer_in_strict_mode() -> None:
    # Strict mode should fail fast when no consumers exist (Routing semantics §5.1).
    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [Y("y")]

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["B"])
    routing = RoutingPort(registry=registry, strict=True)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )

    with pytest.raises(ValueError):
        runner.run()


def test_runner_drops_no_consumer_in_non_strict_mode() -> None:
    # Non-strict mode should drop unconsumed payloads without error.
    seen: list[str] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [Y("y")]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append("B")
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["B"])
    routing = RoutingPort(registry=registry, strict=False)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    assert seen == []


def test_runner_drops_unknown_target_in_non_strict_mode() -> None:
    # Unknown targets should be dropped when routing is non-strict (§5.8).
    seen: list[str] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [Envelope(payload=X("x"), target="Missing")]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append("B")
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["B"])
    routing = RoutingPort(registry=registry, strict=False)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    assert seen == []


def test_runner_avoids_default_self_loop_on_same_token() -> None:
    # Default fan-out should not re-deliver to the same node that emitted the payload.
    counts = {"A": 0}
    seen: list[str] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        counts["A"] += 1
        if counts["A"] == 1:
            return [X("x")]
        return []

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append("B")
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["A", "B"])
    routing = RoutingPort(registry=registry, strict=True)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    assert counts["A"] == 1
    assert seen == ["B"]


def test_runner_requires_explicit_target_for_single_self_consumer_in_strict_mode() -> None:
    # If emitted token is consumed only by the same node, strict mode requires explicit target.
    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        return [X("x")]

    registry = InMemoryConsumerRegistry()
    registry.register(X, ["A"])
    routing = RoutingPort(registry=registry, strict=True)

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="seed", target="A", trace_id="t1"))

    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )

    with pytest.raises(ValueError):
        runner.run()
