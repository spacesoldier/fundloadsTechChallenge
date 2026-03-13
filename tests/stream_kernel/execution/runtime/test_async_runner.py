from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
import time

import pytest

from stream_kernel.execution.runtime.runner import AsyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.observability.domain.debug import DebugMessage
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

    assert observer.trace_by_node.get("n1") == "t1"
    assert observer.trace_by_node.get("n2") == "t1"
    assert observer.trace_by_node.get("n3") == "t1"
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


class _RuntimeDebugBuffer:
    def __init__(self, messages: list[DebugMessage]) -> None:
        self._messages = list(messages)
        self.drain_calls = 0

    def publish(self, message: DebugMessage) -> None:  # pragma: no cover - protocol completeness
        self._messages.append(message)

    def drain(self, *, max_items: int = 256) -> list[DebugMessage]:
        self.drain_calls += 1
        if not self._messages:
            return []
        limit = max(1, int(max_items))
        drained = self._messages[:limit]
        self._messages = self._messages[limit:]
        return drained


def test_async_runner_routes_runtime_debug_messages_on_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    captured: list[DebugMessage] = []

    async def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    async def debug_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, DebugMessage):
            captured.append(payload)
        return []

    registry = InMemoryConsumerRegistry({DebugMessage: ["system.debug.message_dispatch"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    debug_buffer = _RuntimeDebugBuffer(
        [
            DebugMessage(
                timestamp=datetime.now(tz=UTC),
                event="runtime.inject.port_call",
                source="tests.async_runner",
                fields={"method": "send", "payload_model": "X"},
                trace_id="t1",
            )
        ]
    )
    runner = AsyncRunner(
        nodes={
            "worker": worker,
            "system.debug.message_dispatch": debug_dispatch,
        },
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
        runtime_debug_buffer=debug_buffer,
    )

    runner.run()

    assert debug_buffer.drain_calls >= 1
    events = [item.event for item in captured]
    assert "runtime.inject.port_call" in events
    assert "runtime.runner.dequeued" in events
    runner_event = next(
        item
        for item in captured
        if item.event == "runtime.runner.dequeued"
        and item.fields.get("source_node") == "worker"
    )
    assert runner_event.fields.get("payload_model") == "str"
    assert runner_event.fields.get("payload") == "seed"
    assert runner_event.fields.get("stage") == "run_async.loop"


def test_async_runner_routes_runtime_debug_messages_on_error_path() -> None:
    captured: list[DebugMessage] = []

    async def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        raise RuntimeError("boom")

    async def debug_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, DebugMessage):
            captured.append(payload)
        return []

    registry = InMemoryConsumerRegistry({DebugMessage: ["system.debug.message_dispatch"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    debug_buffer = _RuntimeDebugBuffer(
        [
            DebugMessage(
                timestamp=datetime.now(tz=UTC),
                event="runtime.service.call",
                source="tests.async_runner",
                fields={"method": "recv", "payload_model": "Y"},
                trace_id="t1",
            )
        ]
    )
    runner = AsyncRunner(
        nodes={
            "worker": worker,
            "system.debug.message_dispatch": debug_dispatch,
        },
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
        runtime_debug_buffer=debug_buffer,
    )

    with pytest.raises(RuntimeError, match="boom"):
        runner.run()

    assert debug_buffer.drain_calls >= 1
    assert captured == []
    queued = queue.pop()
    assert isinstance(queued, Envelope)
    assert queued.target == "system.debug.message_dispatch"
    assert isinstance(queued.payload, DebugMessage)
    assert queued.payload.event == "runtime.service.call"


def test_async_runner_emits_source_worker_and_lane_for_leaf_ingress_source_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    captured: list[DebugMessage] = []
    source_node = "source:system.cp.root_leaf_ingress:execution.ingress#1:data"

    async def source_worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    async def debug_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, DebugMessage):
            captured.append(payload)
        return []

    registry = InMemoryConsumerRegistry({DebugMessage: ["system.debug.message_dispatch"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target=source_node, trace_id="t1"))
    runner = AsyncRunner(
        nodes={
            source_node: source_worker,
            "system.debug.message_dispatch": debug_dispatch,
        },
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
        runtime_debug_buffer=_RuntimeDebugBuffer([]),
    )

    runner.run()

    runner_event = next(
        item
        for item in captured
        if item.event == "runtime.runner.dequeued"
        and item.fields.get("source_node") == source_node
    )
    assert runner_event.fields.get("node_role") == "source"
    assert runner_event.fields.get("source_worker_id") == "execution.ingress#1"
    assert runner_event.fields.get("source_lane") == "data"


def test_async_runner_stops_on_idle_timeout_without_scheduler_tick_injection() -> None:
    runner = AsyncRunner(
        nodes={},
        work_queue=InMemoryQueue(),
        context_service=_context_service(),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
    )
    started = time.monotonic()
    runner.run_until_stopped(poll_timeout_seconds=0.001, idle_timeout_seconds=1.0)
    assert time.monotonic() >= started


def test_async_runner_does_not_call_node_initialize_implicitly() -> None:
    class _Node:
        def __init__(self) -> None:
            self.init_calls = 0
            self.call_calls = 0

        async def initialize(self) -> None:
            self.init_calls += 1

        async def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
            _ = payload
            self.call_calls += 1
            return []

    node = _Node()
    queue = InMemoryQueue()
    queue.push(Envelope(payload="a", target="n", trace_id="t1"))
    queue.push(Envelope(payload="b", target="n", trace_id="t2"))
    runner = AsyncRunner(
        nodes={"n": node},
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
    )

    runner.run()

    assert node.init_calls == 0
    assert node.call_calls == 2


def test_async_runner_emits_source_lane_for_leaf_command_ingress_source_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STREAM_KERNEL_INJECT_PORT_DEBUG_ENABLED", "1")
    captured: list[DebugMessage] = []
    source_node = "source:system.cp.command_ingress:trace"

    async def source_worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    async def debug_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, DebugMessage):
            captured.append(payload)
        return []

    registry = InMemoryConsumerRegistry({DebugMessage: ["system.debug.message_dispatch"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target=source_node, trace_id="t1"))
    runner = AsyncRunner(
        nodes={
            source_node: source_worker,
            "system.debug.message_dispatch": debug_dispatch,
        },
        work_queue=queue,
        context_service=_context_service(),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
        runtime_debug_buffer=_RuntimeDebugBuffer([]),
    )

    runner.run()

    runner_event = next(
        item
        for item in captured
        if item.event == "runtime.runner.dequeued"
        and item.fields.get("source_node") == source_node
    )
    assert runner_event.fields.get("node_role") == "source"
    assert runner_event.fields.get("source_worker_id") is None
    assert runner_event.fields.get("source_lane") == "trace"
