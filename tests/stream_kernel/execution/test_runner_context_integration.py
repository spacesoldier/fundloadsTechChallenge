from __future__ import annotations

# Context/runner behavior is specified in docs/framework/initial_stage/Execution runtime and routing integration.md.
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.context_store import InMemoryContextStore
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.routing.envelope import Envelope


class _RoutingPortStub:
    # Minimal routing port stub to control downstream deliveries.
    def __init__(self, routes: list[tuple[str, object]]) -> None:
        self._routes = routes

    def route(self, outputs: list[object]) -> list[tuple[str, object]]:
        # Only emit routes when there is actual output to route.
        if not outputs:
            return []
        return list(self._routes)


def test_runner_loads_context_metadata_before_node_call() -> None:
    # Runner should resolve context metadata by trace_id before invoking node (§8.3.2).
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    context_store.put("t1", {"k": "v"})

    work_queue.push(Envelope(payload="msg", target="node", trace_id="t1"))
    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=_RoutingPortStub([]),
    )
    runner.run()
    assert seen == [{"k": "v"}]


def test_runner_passes_empty_metadata_when_missing() -> None:
    # Missing context should yield empty metadata view.
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    work_queue.push(Envelope(payload="msg", target="node", trace_id="missing"))

    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=_RoutingPortStub([]),
    )
    runner.run()
    assert seen == [{}]


def test_runner_propagates_trace_id_to_downstream_nodes() -> None:
    # Downstream nodes should receive the same context metadata.
    seen_a: list[dict[str, object]] = []
    seen_b: list[dict[str, object]] = []

    def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        seen_a.append(ctx)
        return ["out"]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        seen_b.append(ctx)
        return []

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    context_store.put("t1", {"k": "v"})

    work_queue.push(Envelope(payload="msg", target="A", trace_id="t1"))

    # Route A's output to B.
    routing = _RoutingPortStub([("B", "out")])
    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()
    assert seen_a == [{"k": "v"}]
    assert seen_b == [{"k": "v"}]
