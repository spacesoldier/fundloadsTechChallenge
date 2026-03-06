from __future__ import annotations

# Phase E: retire _run_async_blocking from the hot path.
# TraceSinkNode dispatches to emit_async when the sink provides it;
# AsyncRunner awaits the coroutine directly on the running event loop.
import asyncio
import inspect
import threading

from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService


# ---------------------------------------------------------------------------
# Test E1: OTelOtlpTraceSink must expose emit_async
# ---------------------------------------------------------------------------

def test_otlp_sink_has_emit_async_method() -> None:
    # E1: OTelOtlpTraceSink must have an emit_async method (coroutine function).
    from stream_kernel.adapters.trace_sinks import OTelOtlpTraceSink

    sink = OTelOtlpTraceSink(endpoint="http://localhost:4318", backend="httpx", httpx_mode="async")
    assert hasattr(sink, "emit_async"), "OTelOtlpTraceSink must have emit_async"
    assert inspect.iscoroutinefunction(sink.emit_async), "emit_async must be a coroutine function"


# ---------------------------------------------------------------------------
# Test E2: TraceSinkNode dispatches to emit_async (returns coroutine)
# ---------------------------------------------------------------------------

def test_trace_sink_node_returns_coroutine_for_async_sink() -> None:
    # E2: TraceSinkNode.__call__ returns a coroutine when sink has emit_async.
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode

    class _AsyncSink:
        async def emit_async(self, record: object) -> None:
            pass
        def emit(self, record: object) -> None:
            pass
        def flush(self) -> None:
            pass
        def close(self) -> None:
            pass

    node = TraceSinkNode(sink=_AsyncSink())
    result = node(Envelope(payload=object(), target="system.obs.trace_sink"), None)
    assert inspect.iscoroutine(result), (
        "TraceSinkNode must return a coroutine when sink has emit_async"
    )
    # Clean up the unawaited coroutine
    result.close()


# ---------------------------------------------------------------------------
# Test E3: emit_async called — emit NOT called — after coroutine is awaited
# ---------------------------------------------------------------------------

def test_trace_sink_node_dispatches_to_emit_async_not_emit() -> None:
    # E3: after awaiting the coroutine from TraceSinkNode, emit_async called, emit not called.
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode

    emit_calls: list[object] = []
    emit_async_calls: list[object] = []
    record = object()

    class _SpySink:
        async def emit_async(self, rec: object) -> None:
            emit_async_calls.append(rec)
        def emit(self, rec: object) -> None:
            emit_calls.append(rec)
        def flush(self) -> None:
            pass
        def close(self) -> None:
            pass

    node = TraceSinkNode(sink=_SpySink())
    coro = node(Envelope(payload=record, target="system.obs.trace_sink"), None)
    assert inspect.iscoroutine(coro)

    asyncio.run(coro)

    assert emit_async_calls == [record], f"emit_async must be called once with the record, got {emit_async_calls}"
    assert emit_calls == [], f"emit must NOT be called when emit_async is present, got {emit_calls}"


# ---------------------------------------------------------------------------
# Test E4: AsyncRunner awaits emit_async on the running loop — no new thread
# ---------------------------------------------------------------------------

def test_async_runner_awaits_emit_async_on_runner_loop() -> None:
    # E4: AsyncRunner → TraceSinkNode → sink.emit_async awaited on the runner's
    # event loop. No daemon thread is spawned (_run_async_blocking not called).
    from stream_kernel.execution.runtime.runner import AsyncRunner
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode

    runner_loop_box: list[object] = []
    emit_async_loop_box: list[object] = []
    thread_name_box: list[str] = []
    emit_calls: list[object] = []

    class _SpyAsyncSink:
        async def emit_async(self, record: object) -> None:
            emit_async_loop_box.append(asyncio.get_running_loop())
            thread_name_box.append(threading.current_thread().name)
        def emit(self, record: object) -> None:
            emit_calls.append(record)
        def flush(self) -> None:
            pass
        def close(self) -> None:
            pass

    record = object()
    spy = _SpyAsyncSink()
    trace_node = TraceSinkNode(sink=spy)

    queue = InMemoryQueue()
    queue.push(Envelope(payload=record, target="system.obs.trace_sink", trace_id="t1"))

    runner = AsyncRunner(
        nodes={"system.obs.trace_sink": trace_node},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
    )

    async def _run() -> None:
        runner_loop_box.append(asyncio.get_running_loop())
        await runner.run_async()

    asyncio.run(_run())

    # emit_async must have been called exactly once
    assert len(emit_async_loop_box) == 1, f"emit_async must be called once, got {len(emit_async_loop_box)} calls"

    # emit must NOT have been called
    assert emit_calls == [], f"emit must not be called when emit_async present, got {emit_calls}"

    # emit_async must run on the same event loop as the runner (no new event loop)
    assert emit_async_loop_box[0] is runner_loop_box[0], (
        "emit_async must be awaited on the runner's event loop, not a new one"
    )

    # emit_async must run in the main thread (no daemon thread spawned)
    assert thread_name_box[0] == threading.main_thread().name, (
        f"emit_async must run in main thread, got {thread_name_box[0]!r}"
    )


def test_trace_dispatch_node_uses_publish_trace_async_on_async_runner_loop() -> None:
    from stream_kernel.execution.orchestration.observability_system_nodes import (
        TraceDispatchNode,
    )
    from stream_kernel.observability.events import TraceDispatchEvent

    loop_box: list[object] = []
    called_box: list[str] = []

    class _Pipeline:
        async def publish_trace_async(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            _ = (event, trace_id, attributes)
            loop_box.append(asyncio.get_running_loop())
            called_box.append("async")

        def publish_trace(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            _ = (event, trace_id, attributes)
            called_box.append("sync")

    node = TraceDispatchNode(pipeline=_Pipeline())

    async def _run() -> None:
        running_loop = asyncio.get_running_loop()
        result = node(TraceDispatchEvent(payload={"span": "n1"}, trace_id="t1"), None)
        assert inspect.iscoroutine(result)
        await result
        assert loop_box[0] is running_loop

    asyncio.run(_run())
    assert called_box == ["async"]


def test_trace_dispatch_node_uses_emit_trace_event_when_available() -> None:
    from stream_kernel.execution.orchestration.observability_system_nodes import (
        TraceDispatchNode,
    )
    from stream_kernel.observability.domain.logging import LogMessage
    from stream_kernel.observability.events import LogDispatchEvent, TraceDispatchEvent

    class _Pipeline:
        def emit_trace_event(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> list[object]:
            _ = (event, trace_id, attributes)
            return [
                LogDispatchEvent(
                    payload=LogMessage(level="error", message="sink-failed"),
                    trace_id=trace_id,
                    attributes={"source_node": "system.obs.trace_dispatch"},
                )
            ]

        def publish_trace(
            self,
            *,
            event: object,
            trace_id: str | None = None,
            attributes: dict[str, object] | None = None,
        ) -> None:
            raise AssertionError("publish_trace must not be called when emit_trace_event is available")

    node = TraceDispatchNode(pipeline=_Pipeline())
    produced = node(TraceDispatchEvent(payload={"span": "n1"}, trace_id="t1"), None)
    assert isinstance(produced, list)
    assert len(produced) == 1
    assert isinstance(produced[0], LogDispatchEvent)
