from __future__ import annotations

# Context/runner behavior is specified in docs/framework/initial_stage/Execution runtime and routing integration.md.
import pytest

from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.execution.runtime.runner import SyncRunner
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult


class _RoutingPortStub:
    # Minimal routing port stub to control downstream deliveries.
    def __init__(self, routes: list[tuple[str, object]]) -> None:
        self._routes = routes

    def route(self, outputs: list[object], *, source: str | None = None) -> RoutingResult:
        # Only emit routes when there is actual output to route.
        if not outputs:
            return RoutingResult(local_deliveries=[])
        return RoutingResult(local_deliveries=list(self._routes))


def test_runner_loads_context_metadata_before_node_call() -> None:
    # Runner should resolve context metadata by trace_id before invoking node (§8.3.2).
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"k": "v"})
    context_service = InMemoryKvContextService(store)

    work_queue.push(Envelope(payload="msg", target="node", trace_id="t1"))
    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
    )
    runner.run()
    assert seen == [{"k": "v"}]


def test_runner_hides_internal_context_keys_from_regular_nodes() -> None:
    # Regular nodes should get metadata-only context without framework-internal keys.
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"line_no": 1, "__trace_id": "t1", "__run_id": "run", "k": "v"})
    context_service = InMemoryKvContextService(store)

    work_queue.push(Envelope(payload="msg", target="node", trace_id="t1"))
    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
    )
    runner.run()
    assert seen == [{"line_no": 1, "k": "v"}]


def test_runner_passes_full_context_to_service_nodes() -> None:
    # Service nodes are allowed to receive full context (including internal keys).
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"line_no": 1, "__trace_id": "t1", "__run_id": "run", "k": "v"})
    context_service = InMemoryKvContextService(store)

    work_queue.push(Envelope(payload="msg", target="node", trace_id="t1"))
    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        full_context_nodes={"node"},
        observability=NoOpObservabilityService(),
    )
    runner.run()
    assert seen == [{"line_no": 1, "__trace_id": "t1", "__run_id": "run", "k": "v"}]


def test_runner_passes_empty_metadata_when_missing() -> None:
    # Missing context should yield empty metadata view.
    seen: list[dict[str, object]] = []

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        seen.append(ctx)
        return []

    work_queue = InMemoryQueue()
    context_service = InMemoryKvContextService(InMemoryKvStore())
    work_queue.push(Envelope(payload="msg", target="node", trace_id="missing"))

    runner = SyncRunner(
        nodes={"node": node},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
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

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"k": "v"})
    context_service = InMemoryKvContextService(store)

    work_queue.push(Envelope(payload="msg", target="A", trace_id="t1"))

    # Route A's output to B.
    routing = _RoutingPortStub([("B", "out")])
    runner = SyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=work_queue,
        context_service=context_service,
        router=routing,
        observability=NoOpObservabilityService(),
    )
    runner.run()
    assert seen_a == [{"k": "v"}]
    assert seen_b == [{"k": "v"}]


def test_runner_source_seq_mode_requires_seq_for_sink_nodes() -> None:
    # Ordered sink mode must fail when sink delivery context has no transport seq.
    seen: list[object] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        seen.append(payload)
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"k": "v"})  # no __seq
    context_service = InMemoryKvContextService(store)
    work_queue.push(Envelope(payload="msg", target="sink:writer", trace_id="t1"))

    runner = SyncRunner(
        nodes={"sink:writer": sink},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
        ordered_sink_mode="source_seq",
    )

    with pytest.raises(ValueError):
        runner.run()
    assert seen == []


def test_runner_source_seq_mode_accepts_seq_for_sink_nodes() -> None:
    # Ordered sink mode should allow sink processing when __seq is present.
    seen: list[object] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        seen.append(payload)
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t1", {"__seq": 3, "k": "v"})
    context_service = InMemoryKvContextService(store)
    work_queue.push(Envelope(payload="msg", target="sink:writer", trace_id="t1"))

    runner = SyncRunner(
        nodes={"sink:writer": sink},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
        ordered_sink_mode="source_seq",
    )
    runner.run()
    assert seen == ["msg"]


def test_runner_completion_mode_keeps_current_queue_order_without_reorder() -> None:
    # Characterization: without reorder engine, sink observes queue/completion order as-is.
    observed_seq: list[int] = []

    def sink(_payload: object, ctx: dict[str, object]) -> list[object]:
        observed_seq.append(int(ctx["seq"]))
        return []

    work_queue = InMemoryQueue()
    store = InMemoryKvStore()
    store.set("t2", {"seq": 2})
    store.set("t1", {"seq": 1})
    context_service = InMemoryKvContextService(store)
    work_queue.push(Envelope(payload="second", target="sink:writer", trace_id="t2"))
    work_queue.push(Envelope(payload="first", target="sink:writer", trace_id="t1"))

    runner = SyncRunner(
        nodes={"sink:writer": sink},
        work_queue=work_queue,
        context_service=context_service,
        router=_RoutingPortStub([]),
        observability=NoOpObservabilityService(),
        ordered_sink_mode="completion",
    )
    runner.run()
    assert observed_seq == [2, 1]
