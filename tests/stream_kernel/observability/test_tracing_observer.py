from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.execution.runtime.runner import SyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.kernel.trace import TraceRecorder
from stream_kernel.execution.orchestration.observability_system_nodes import TraceDispatchEvent
from stream_kernel.execution.orchestration.observability_system_nodes import TraceDispatchNode
from stream_kernel.observability.observers.tracing import TracingObserver, _FanoutTraceSink
from stream_kernel.platform.services.observability import FanoutObservabilityService
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


def test_tracing_observer_captures_route_markers_from_context() -> None:
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"n1": 0},
    )

    ctx = {
        "__process_group": "execution.features",
        "__handoff_from": "execution.ingress",
        "__route_hop": 2,
        "__parent_span_id": "0123456789abcdef",
        "__runner_gap_ms": 12.5,
    }
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
    assert record.route is not None
    assert record.route.process_group == "execution.features"
    assert record.route.handoff_from == "execution.ingress"
    assert record.route.route_hop == 2
    assert record.route.parent_span_id == "0123456789abcdef"
    assert record.route.runner_gap_ms == 12.5


def test_tracing_observer_prefers_previous_trace_step_gap_over_runner_gap_hint() -> None:
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"n1": 0, "n2": 1},
    )

    # First step seeds trace history.
    state1 = observer.before_node(node_name="n1", payload={"id": "1"}, ctx={}, trace_id="t1")
    observer.after_node(
        node_name="n1",
        payload={"id": "1"},
        ctx={},
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state1,
    )

    # Second step provides an exaggerated runner hint; observer should prefer
    # previous trace-step delta for the same trace.
    state2 = observer.before_node(
        node_name="n2",
        payload={"id": "1"},
        ctx={"__runner_gap_ms": 999.0},
        trace_id="t1",
    )
    observer.after_node(
        node_name="n2",
        payload={"id": "1"},
        ctx={"__runner_gap_ms": 999.0},
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state2,
    )

    second = sink.records[-1]
    assert second.route is not None
    assert second.route.runner_gap_ms is not None
    assert second.route.runner_gap_ms != 999.0


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
        router=RoutingService(registry=registry, strict=True),
        observability=FanoutObservabilityService(observers=[observer]),
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
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=FanoutObservabilityService(observers=[observer]),
    )
    runner.run()

    assert emitted == ["seed"]
    assert [record.step_name for record in sink.records] == ["worker"]


def test_tracing_observer_skips_excluded_nodes() -> None:
    # Phase B3: TracingObserver with excluded_node_names must not trace those nodes.
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"system.obs.trace_sink": 0, "worker": 1},
        excluded_node_names=frozenset({"system.obs.trace_sink"}),
    )

    # Excluded node: before_node must return None; after_node must be a no-op.
    state = observer.before_node(
        node_name="system.obs.trace_sink",
        payload={},
        ctx={},
        trace_id="t1",
    )
    assert state is None, "before_node must return None for excluded nodes"
    observer.after_node(
        node_name="system.obs.trace_sink",
        payload={},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert len(sink.records) == 0, "excluded node must not generate a trace record"

    # Non-excluded node: must still trace normally.
    state2 = observer.before_node(node_name="worker", payload={"id": "1"}, ctx={}, trace_id="t2")
    observer.after_node(
        node_name="worker",
        payload={"id": "1"},
        ctx={},
        trace_id="t2",
        outputs=[],
        state=state2,
    )
    assert len(sink.records) == 1


def test_tracing_observer_skips_system_observability_nodes_by_reserved_prefix() -> None:
    # OBS-L-02: `system.obs.*` nodes are always trace-silent to prevent recursive self-observation.
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={
            "system.obs.trace_dispatch": 0,
            "system.obs.metric_dispatch:obs.async": 1,
            "worker": 2,
        },
    )

    for node_name in ("system.obs.trace_dispatch", "system.obs.metric_dispatch:obs.async"):
        state = observer.before_node(node_name=node_name, payload={}, ctx={}, trace_id="t1")
        assert state is None
        observer.after_node(
            node_name=node_name,
            payload={},
            ctx={},
            trace_id="t1",
            outputs=[],
            state=state,
        )

    # Business node remains traced.
    state = observer.before_node(node_name="worker", payload={}, ctx={}, trace_id="t1")
    observer.after_node(
        node_name="worker",
        payload={},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )
    assert len(sink.records) == 1


def test_tracing_observer_default_excluded_node_names_is_empty() -> None:
    # Phase B3: TracingObserver without excluded_node_names traces everything normally.
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
    )
    state = observer.before_node(node_name="worker", payload={}, ctx={}, trace_id="t1")
    observer.after_node(node_name="worker", payload={}, ctx={}, trace_id="t1", outputs=[], state=state)
    assert len(sink.records) == 1


# ---------------------------------------------------------------------------
# Phase C: routing-native trace emission
# ---------------------------------------------------------------------------


def test_trace_after_node_routes_to_queue_not_sink() -> None:
    # Phase C2: when trace_queue is provided, after_node() must push to queue, NOT call sink.emit().
    recorder = TraceRecorder()
    sink = _Sink()
    queue = InMemoryQueue()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
        trace_queue=queue,
    )

    state = observer.before_node(node_name="worker", payload={"id": "x"}, ctx={}, trace_id="t1")
    observer.after_node(
        node_name="worker",
        payload={"id": "x"},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )

    assert len(sink.records) == 0, "sink.emit() must NOT be called when trace_queue is set"
    msg = queue.pop()
    assert msg is not None, "queue must contain a routed TraceRecord envelope"
    assert isinstance(msg, Envelope)
    assert msg.target == "system.obs.trace_sink"
    assert hasattr(msg.payload, "step_name"), "payload must be a TraceRecord"
    assert msg.payload.step_name == "worker"


def test_tracing_observer_can_emit_trace_dispatch_event_for_runner_dispatch_mode() -> None:
    # OBS-L-03: in runner-dispatch mode observer emits service message, not direct sink I/O.
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
        emit_via_runner=True,
    )

    state = observer.before_node(node_name="worker", payload={"id": "x"}, ctx={}, trace_id="t1")
    dispatched = observer.after_node(
        node_name="worker",
        payload={"id": "x"},
        ctx={},
        trace_id="t1",
        outputs=[],
        state=state,
    )

    assert len(sink.records) == 0
    assert isinstance(dispatched, TraceDispatchEvent)
    assert dispatched.trace_id == "t1"


def test_runner_dispatch_mode_routes_trace_event_without_system_obs_recursion() -> None:
    # OBS-L-01/02: runner routes trace dispatch event; system.obs node execution stays trace-silent.
    recorder = TraceRecorder()
    sink = _Sink()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0, "system.obs.trace_dispatch": 1},
        emit_via_runner=True,
    )
    observability = FanoutObservabilityService(observers=[observer])

    routed: list[object] = []

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        routed.append(payload)
        return []

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="worker", trace_id="t1"))
    registry = InMemoryConsumerRegistry({TraceDispatchEvent: ["system.obs.trace_dispatch"]})

    runner = SyncRunner(
        nodes={
            "worker": worker,
            "system.obs.trace_dispatch": TraceDispatchNode(pipeline=observability, qualifier=None),
        },
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=observability,
    )
    runner.run()

    assert routed == ["seed"]
    assert len(sink.records) == 1
    assert sink.records[0].step_name == "worker"


def test_trace_on_node_error_routes_to_queue_not_sink() -> None:
    # Phase C2: on_node_error() also routes through queue when trace_queue is set.
    recorder = TraceRecorder()
    sink = _Sink()
    queue = InMemoryQueue()
    observer = TracingObserver(
        recorder=recorder,
        sink=sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
        trace_queue=queue,
    )

    state = observer.before_node(node_name="worker", payload={}, ctx={}, trace_id="t1")
    observer.on_node_error(
        node_name="worker",
        payload={},
        ctx={},
        trace_id="t1",
        error=RuntimeError("boom"),
        state=state,
    )

    assert len(sink.records) == 0
    msg = queue.pop()
    assert msg is not None
    assert isinstance(msg, Envelope)
    assert msg.target == "system.obs.trace_sink"
    assert msg.payload.status == "error"


def test_trace_on_run_end_skips_flush_when_queue_routed() -> None:
    # Phase C2: on_run_end() must NOT flush/close the sink when trace_queue is provided.
    # Sink lifecycle is owned by TraceSinkNode after Phase C.
    @dataclass
    class _TrackingSink:
        records: list[object] = field(default_factory=list)
        flushed: bool = False
        closed: bool = False

        def emit(self, record: object) -> None:
            self.records.append(record)

        def flush(self) -> None:
            self.flushed = True

        def close(self) -> None:
            self.closed = True

    recorder = TraceRecorder()
    tracking_sink = _TrackingSink()
    queue = InMemoryQueue()
    observer = TracingObserver(
        recorder=recorder,
        sink=tracking_sink,
        run_id="run",
        scenario_id="s1",
        step_indices={},
        trace_queue=queue,
    )
    observer.on_run_end()

    assert not tracking_sink.flushed, "sink.flush() must not be called when queue-routed"
    assert not tracking_sink.closed, "sink.close() must not be called when queue-routed"


def test_trace_sink_node_processes_trace_record_from_queue() -> None:
    # Phase C: TraceSinkNode.__call__ with a TraceRecord envelope calls sink.emit() once.
    from stream_kernel.execution.orchestration.observability_system_nodes import TraceSinkNode

    recorder = TraceRecorder()
    capture_sink = _Sink()
    queue = InMemoryQueue()
    observer = TracingObserver(
        recorder=recorder,
        sink=_Sink(),  # observer-side sink unused (queue routed)
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
        trace_queue=queue,
    )

    state = observer.before_node(node_name="worker", payload={"id": "1"}, ctx={}, trace_id="t1")
    observer.after_node(
        node_name="worker",
        payload={"id": "1"},
        ctx={},
        trace_id="t1",
        outputs=[{"ok": True}],
        state=state,
    )

    envelope = queue.pop()
    assert envelope is not None

    trace_sink_node = TraceSinkNode(sink=capture_sink)
    result = trace_sink_node(envelope, None)

    assert result == []
    assert len(capture_sink.records) == 1
    record = capture_sink.records[0]
    assert record.step_name == "worker"
    assert record.status == "ok"


def test_tracing_fanout_exporter_failure_is_isolated_from_business_execution() -> None:
    # OBS-MAT-04: exporter/sink failure must not break business pipeline when fanout sink is used.
    emitted: list[str] = []

    class _FailingSink:
        def emit(self, record: object) -> None:
            _ = record
            raise RuntimeError("exporter down")

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    recorder = TraceRecorder()
    ok_sink = _Sink()
    fanout_sink = _FanoutTraceSink([_FailingSink(), ok_sink])
    observer = TracingObserver(
        recorder=recorder,
        sink=fanout_sink,
        run_id="run",
        scenario_id="s1",
        step_indices={"worker": 0},
    )

    def worker(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        emitted.append(str(payload))
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
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=FanoutObservabilityService(observers=[observer]),
    )
    runner.run()

    assert emitted == ["seed"]
    assert len(ok_sink.records) == 1
