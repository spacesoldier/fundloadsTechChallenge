from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.execution.orchestration.observability_system_nodes import TraceDispatchEvent
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.routing.envelope import Envelope


@dataclass
class _Observer:
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_end_calls: int = 0

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict[str, object],
        trace_id: str | None,
    ) -> str:
        self.before.append(node_name)
        assert trace_id == "t1"
        return f"state:{node_name}"

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
        assert state == f"state:{node_name}"
        assert trace_id == "t1"
        self.after.append(node_name)

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
        assert state == f"state:{node_name}"
        assert trace_id == "t1"
        self.errors.append(type(error).__name__)

    def on_run_end(self) -> None:
        self.run_end_calls += 1


def _routing() -> RoutingService:
    return RoutingService(registry=InMemoryConsumerRegistry(), strict=True)


def test_runner_notifies_observer_on_success_path() -> None:
    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        return []

    observer = _Observer()
    queue = InMemoryQueue()
    queue.push(Envelope(payload="x", target="n1", trace_id="t1"))
    runner = SyncRunner(
        nodes={"n1": node},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=_routing(),
        observability=observer,
    )
    runner.run()

    assert observer.before == ["n1"]
    assert observer.after == ["n1"]
    assert observer.errors == []


def test_runner_notifies_observer_on_error_path() -> None:
    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        raise RuntimeError("boom")

    observer = _Observer()
    queue = InMemoryQueue()
    queue.push(Envelope(payload="x", target="n1", trace_id="t1"))
    runner = SyncRunner(
        nodes={"n1": node},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=_routing(),
        observability=observer,
    )
    with pytest.raises(RuntimeError):
        runner.run()

    assert observer.before == ["n1"]
    assert observer.after == []
    assert observer.errors == ["RuntimeError"]


def test_runner_noop_observability_is_valid_runtime_dependency() -> None:
    # Runner should work with platform default no-op observability service.
    queue = InMemoryQueue()
    queue.push(Envelope(payload="x", target="n1", trace_id="t1"))

    def node(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    runner = SyncRunner(
        nodes={"n1": node},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=_routing(),
        observability=NoOpObservabilityService(),
    )
    runner.run()


def test_runner_calls_observer_on_run_end_hook() -> None:
    # Characterization: run lifecycle completion is driven by explicit runner.on_run_end() call.
    observer = _Observer()
    runner = SyncRunner(
        nodes={},
        work_queue=InMemoryQueue(),
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=_routing(),
        observability=observer,
    )

    runner.on_run_end()

    assert observer.run_end_calls == 1


def test_runner_propagates_parent_and_current_span_ids_across_messages() -> None:
    @dataclass(frozen=True, slots=True)
    class Event:
        value: str

    class _SpanObserver:
        def __init__(self) -> None:
            self._seen_parent_by_node: dict[str, str | None] = {}

        def before_node(self, *, node_name: str, payload: object, ctx: dict[str, object], trace_id: str | None):
            _ = (payload, trace_id)
            parent = ctx.get("__parent_span_id")
            self._seen_parent_by_node[node_name] = parent if isinstance(parent, str) else None
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
    registry = InMemoryConsumerRegistry({Event: ["n2"]})
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1", span_id="upstream-parent"))

    seen: list[str] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [Event(value="x")]

    def n2(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, Event):
            seen.append(payload.value)
        return []

    runner = SyncRunner(
        nodes={"n1": n1, "n2": n2},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=observer,
    )
    runner.run()

    assert seen == ["x"]
    assert observer._seen_parent_by_node["n1"] == "upstream-parent"
    assert observer._seen_parent_by_node["n2"] == "span-n1"


def test_runner_routes_observability_service_outputs_via_router_queue() -> None:
    # OBS-L-01: service outputs returned by observability callbacks must be routed by runner rails.
    routed: list[object] = []

    class _DispatchingObserver:
        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> object | None:
            _ = (node_name, payload, ctx, trace_id)
            return None

        def after_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
            outputs: list[object],
            state: object | None,
        ) -> list[object] | None:
            _ = (payload, ctx, outputs, state)
            if node_name != "worker":
                return None
            return [
                TraceDispatchEvent(
                    payload={"kind": "trace", "node": node_name},
                    trace_id=trace_id,
                )
            ]

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
            return None

        def on_run_end(self) -> None:
            return None

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    def trace_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        routed.append(payload)
        return []

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    registry = InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]})

    runner = SyncRunner(
        nodes={
            "worker": worker,
            "system.obs.trace_dispatch": trace_dispatch,
        },
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=_DispatchingObserver(),
    )
    runner.run()

    assert len(routed) == 1
    dispatched = routed[0]
    assert isinstance(dispatched, TraceDispatchEvent)
    assert dispatched.trace_id == "t1"


def test_runner_autofills_trace_id_and_attributes_for_node_emitted_trace_dispatch_event() -> None:
    routed: list[object] = []

    class _DispatchingObserver:
        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> object | None:
            _ = (node_name, payload, ctx, trace_id)
            return None

        def after_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
            outputs: list[object],
            state: object | None,
        ) -> list[object] | None:
            _ = (payload, ctx, outputs, state)
            if node_name != "worker":
                return None
            return [TraceDispatchEvent(payload={"kind": "trace"})]

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
            return None

        def on_run_end(self) -> None:
            return None

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    def trace_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        routed.append(payload)
        return []

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    registry = InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]})

    runner = SyncRunner(
        nodes={
            "worker": worker,
            "system.obs.trace_dispatch": trace_dispatch,
        },
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=_DispatchingObserver(),
    )
    runner.run()

    assert len(routed) == 1
    dispatched = routed[0]
    assert isinstance(dispatched, TraceDispatchEvent)
    assert dispatched.trace_id == "t1"
    assert dispatched.attributes.get("source_node") == "worker"
    assert dispatched.attributes.get("event_payload_type") == "dict"
    assert dispatched.attributes.get("correlation_id") == "t1"


def test_runner_guard_prevents_observability_self_dispatch_recursion_on_system_nodes() -> None:
    # RUN-UNI-D1: system.obs.* execution must not re-enter observability callbacks.
    system_exec_calls = 0

    class _RecursiveObserver:
        def __init__(self) -> None:
            self.before: list[str] = []
            self.after: list[str] = []
            self.calls = 0

        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> object | None:
            _ = (payload, ctx, trace_id)
            self.before.append(node_name)
            return None

        def after_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
            outputs: list[object],
            state: object | None,
        ) -> list[object] | None:
            _ = (payload, ctx, outputs, state)
            self.calls += 1
            self.after.append(node_name)
            if self.calls > 3:
                raise RuntimeError("observability recursion detected")
            return [TraceDispatchEvent(payload={"from": node_name}, trace_id=trace_id)]

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
            return None

        def on_run_end(self) -> None:
            return None

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return []

    def trace_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
        nonlocal system_exec_calls
        _ = (payload, ctx)
        system_exec_calls += 1
        return []

    observer = _RecursiveObserver()
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    registry = InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]})
    runner = SyncRunner(
        nodes={
            "worker": worker,
            "system.obs.trace_dispatch": trace_dispatch,
        },
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=observer,
    )
    runner.run()

    assert observer.before == ["worker"]
    assert observer.after == ["worker"]
    assert system_exec_calls == 1


@pytest.mark.parametrize("runner_kind", ["sync", "async"])
def test_system_observability_node_exclusion_is_deterministic_for_sync_and_async(
    runner_kind: str,
) -> None:
    # RUN-UNI-D2: `system.obs.*` exclusion must behave the same in sync and async runners.
    observed_before: list[str] = []
    observed_after: list[str] = []

    class _DispatchingObserver:
        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> object | None:
            _ = (payload, ctx, trace_id)
            observed_before.append(node_name)
            return None

        def after_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
            outputs: list[object],
            state: object | None,
        ) -> list[object] | None:
            _ = (payload, ctx, outputs, state)
            observed_after.append(node_name)
            if node_name == "worker":
                return [TraceDispatchEvent(payload={"node": node_name}, trace_id=trace_id)]
            return None

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
            return None

        def on_run_end(self) -> None:
            return None

    if runner_kind == "async":
        async def worker(payload: object, ctx: dict[str, object]) -> list[object]:
            _ = (payload, ctx)
            return []

        async def trace_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
            _ = (payload, ctx)
            return []

        runner = AsyncRunner(
            nodes={"worker": worker, "system.obs.trace_dispatch": trace_dispatch},
            work_queue=InMemoryQueue(),
            context_service=InMemoryKvContextService(InMemoryKvStore()),
            router=RoutingService(
                registry=InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]}),
                strict=True,
            ),
            observability=_DispatchingObserver(),
        )
    else:
        def worker(payload: object, ctx: dict[str, object]) -> list[object]:
            _ = (payload, ctx)
            return []

        def trace_dispatch(payload: object, ctx: dict[str, object]) -> list[object]:
            _ = (payload, ctx)
            return []

        runner = SyncRunner(
            nodes={"worker": worker, "system.obs.trace_dispatch": trace_dispatch},
            work_queue=InMemoryQueue(),
            context_service=InMemoryKvContextService(InMemoryKvStore()),
            router=RoutingService(
                registry=InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]}),
                strict=True,
            ),
            observability=_DispatchingObserver(),
        )

    runner.work_queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    runner.run()
    assert observed_before == ["worker"]
    assert observed_after == ["worker"]
