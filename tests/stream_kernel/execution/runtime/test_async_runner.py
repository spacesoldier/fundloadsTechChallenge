from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from stream_kernel.execution.runtime.runner import AsyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService


def _context_service() -> InMemoryKvContextService:
    return InMemoryKvContextService(InMemoryKvStore())


@dataclass(frozen=True, slots=True)
class _X:
    value: str


@dataclass(frozen=True, slots=True)
class _Y:
    value: str


def test_async_runner_executes_async_node_and_routes_outputs() -> None:
    # RUN-ASYNC-01: awaitable node path should execute and route outputs through routing service.
    seen: list[_X] = []

    async def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [_X("ok")]

    def node_b(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, _X):
            seen.append(payload)
        return []

    registry = InMemoryConsumerRegistry({_X: ["B"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="A", trace_id="t1"))
    runner = AsyncRunner(
        nodes={"A": node_a, "B": node_b},
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
    )
    runner.run()

    assert seen == [_X("ok")]


def test_async_runner_preserves_trace_continuity_across_sync_async_sync_chain() -> None:
    # RUN-ASYNC-02: trace/span continuity should remain deterministic across mixed sync/async node chain.
    class _SpanObserver:
        def __init__(self) -> None:
            self.parent_by_node: dict[str, str | None] = {}
            self.trace_by_node: dict[str, str | None] = {}

        def before_node(self, *, node_name: str, payload: object, ctx: dict[str, object], trace_id: str | None):
            _ = payload
            parent = ctx.get("__parent_span_id")
            self.parent_by_node[node_name] = parent if isinstance(parent, str) else None
            self.trace_by_node[node_name] = trace_id
            return SimpleNamespace(span=SimpleNamespace(span_id=f"span-{node_name}"))

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
            _ = (node_name, payload, ctx, trace_id, outputs, state)

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
            _ = (node_name, payload, ctx, trace_id, error, state)

        def on_run_end(self) -> None:
            return None

    observer = _SpanObserver()

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [_X("x")]

    async def n2(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [_Y("y")]

    def n3(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    registry = InMemoryConsumerRegistry({_X: ["n2"], _Y: ["n3"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1", span_id="upstream-parent"))
    runner = AsyncRunner(
        nodes={"n1": n1, "n2": n2, "n3": n3},
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=observer,
    )
    runner.run()

    assert observer.trace_by_node == {"n1": "t1", "n2": "t1", "n3": "t1"}
    assert observer.parent_by_node["n1"] == "upstream-parent"
    assert observer.parent_by_node["n2"] == "span-n1"
    assert observer.parent_by_node["n3"] == "span-n2"


def test_async_runner_stop_request_drains_inflight_queue_deterministically() -> None:
    # RUN-ASYNC-03: cancellation/stop should drain inflight queue deterministically when drain_on_stop=True.
    seen: list[int] = []
    runner: AsyncRunner | None = None

    async def node_a(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, int):
            seen.append(payload)
            if payload == 1 and runner is not None:
                runner.request_stop()
        return []

    queue = InMemoryQueue()
    queue.push(Envelope(payload=1, target="A", trace_id="t1"))
    queue.push(Envelope(payload=2, target="A", trace_id="t2"))
    runner = AsyncRunner(
        nodes={"A": node_a},
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
        drain_on_stop=True,
    )

    runner.run()
    assert seen == [1, 2]
