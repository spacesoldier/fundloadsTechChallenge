from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.platform.services.context import InMemoryKvContextService
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.execution.observability_service import ObserverBackedObservabilityService
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.kernel.trace import TraceRecorder
from stream_kernel.observability.observers.tracing import TracingObserver
from stream_kernel.routing.envelope import Envelope


@dataclass
class _Sink:
    records: list[object] = field(default_factory=list)

    def emit(self, record: object) -> None:
        self.records.append(record)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_tracing_observer_emits_trace_record_on_success() -> None:
    recorder = TraceRecorder(
        signature_mode="type_only",
        context_diff_mode="whitelist",
        context_diff_whitelist=["run_id"],
    )
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"n1": 0},
    )

    ctx = {"any": "metadata"}
    state = observer.before_node(node_name="n1", payload={"id": "1"}, ctx=ctx, trace_id="t1")
    observer.after_node(
        node_name="n1",
        payload={"id": "1"},
        ctx=ctx,
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state,
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.trace_id == "t1"
    assert record.step_name == "n1"
    assert record.status == "ok"
    assert record.ctx_before == {"run_id": "run"}


def test_tracing_observer_emits_error_record() -> None:
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"n1": 0},
    )

    ctx = {"any": "metadata"}
    state = observer.before_node(node_name="n1", payload={"id": "1"}, ctx=ctx, trace_id="t1")
    observer.on_node_error(
        node_name="n1",
        payload={"id": "1"},
        ctx=ctx,
        trace_id="t1",
        error=RuntimeError("boom"),
        state=state,
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.trace_id == "t1"
    assert record.step_name == "n1"
    assert record.status == "error"
    assert record.error is not None
    assert record.error.type == "RuntimeError"


def test_tracing_observer_skips_messages_without_trace_id() -> None:
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"n1": 0},
    )

    state = observer.before_node(node_name="n1", payload={"id": "1"}, ctx={}, trace_id=None)
    observer.after_node(
        node_name="n1",
        payload={"id": "1"},
        ctx={},
        trace_id=None,
        outputs=[],
        state=state,
    )

    assert state is None
    assert sink.records == []


def test_tracing_records_span_for_adapter_node_executed_in_graph() -> None:
    # Tracing runtime §4.1: adapter-node executed by runner is traced as normal node span.
    @dataclass(frozen=True, slots=True)
    class Event:
        value: str

    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"adapter_source": 0, "worker": 1},
    )

    def adapter_source(payload: object, ctx: dict[str, object]) -> list[object]:
        return [Event("e1")]

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        return []

    registry = InMemoryConsumerRegistry()
    registry.register(Event, ["worker"])

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="adapter_source", trace_id="t1"))
    context_store = InMemoryKvStore()
    context_store.set("t1", {"run_id": "run"})
    context_service = InMemoryKvContextService(context_store)

    runner = SyncRunner(
        nodes={"adapter_source": adapter_source, "worker": worker},
        work_queue=queue,
        context_service=context_service,
        router=RoutingPort(registry=registry, strict=True),
        observability=ObserverBackedObservabilityService(observers=[observer]),
    )
    runner.run()

    assert [record.step_name for record in sink.records] == ["adapter_source", "worker"]


def test_tracing_does_not_add_extra_span_for_injected_adapter_call() -> None:
    # Tracing runtime §4.1: injected adapter call stays inside caller node span by default.
    emitted: list[str] = []

    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
    )

    class _InjectedAdapter:
        def write(self, value: object) -> None:
            emitted.append(str(value))

    injected = _InjectedAdapter()

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        # Adapter is invoked inside node code and must not produce a standalone node span.
        injected.write(payload)
        return []

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    context_store = InMemoryKvStore()
    context_store.set("t1", {"run_id": "run"})
    context_service = InMemoryKvContextService(context_store)

    runner = SyncRunner(
        nodes={"worker": worker},
        work_queue=queue,
        context_service=context_service,
        router=RoutingPort(registry=InMemoryConsumerRegistry(), strict=True),
        observability=ObserverBackedObservabilityService(observers=[observer]),
    )
    runner.run()

    assert emitted == ["seed"]
    assert [record.step_name for record in sink.records] == ["worker"]
