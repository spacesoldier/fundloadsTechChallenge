from __future__ import annotations

# Runner interface is part of the execution model (Execution runtime + planning docs).
import pytest

from stream_kernel.application_context.application_context import apply_injection
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService,
)
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.runtime.runner_port import RunnerPort
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue, QueuePort
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.routing.envelope import Envelope
from stream_kernel.platform.services.messaging.reply_waiter import TerminalEvent
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDeferredMessageHoldEvent,
)
from stream_kernel.execution.runtime.runner_ingress import enqueue_runner_input_sync
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
import threading
import time


def _build_sync_runner() -> SyncRunner:
    # Minimal runner instance for interface checks.
    registry = InMemoryConsumerRegistry({})
    routing = RoutingService(registry=registry, strict=True)
    context_service = InMemoryKvContextService(InMemoryKvStore())
    return SyncRunner(
        nodes={},
        work_queue=InMemoryQueue(),
        context_service=context_service,
        router=routing,
        observability=NoOpObservabilityService(),
    )


def test_sync_runner_implements_runner_port() -> None:
    # SyncRunner should conform to the RunnerPort interface.
    runner = _build_sync_runner()
    assert isinstance(runner, RunnerPort)


def test_async_runner_implements_runner_port() -> None:
    # AsyncRunner should conform to the same RunnerPort interface via run() facade.
    registry = InMemoryConsumerRegistry({})
    routing = RoutingService(registry=registry, strict=True)
    runner = AsyncRunner(
        nodes={},
        work_queue=InMemoryQueue(),
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=routing,
        observability=NoOpObservabilityService(),
    )
    assert isinstance(runner, RunnerPort)


def test_sync_runner_uses_routing_service_injection_contract() -> None:
    # Step-B contract: runner should request RoutingService from DI.
    assert SyncRunner.__dataclass_fields__["router"].default.data_type is RoutingService


def test_non_runner_does_not_match_port() -> None:
    # Objects without a run() method should not satisfy RunnerPort.
    class NotRunner:
        pass

    assert isinstance(NotRunner(), RunnerPort) is False


def test_runner_port_run_returns_none() -> None:
    # Runner.run() is a fire-and-forget operation with no return value.
    runner = _build_sync_runner()
    assert runner.run() is None


def test_runners_do_not_expose_run_inputs_bootstrap_shortcut() -> None:
    assert not hasattr(SyncRunner, "run_inputs")
    assert not hasattr(AsyncRunner, "run_inputs")


def test_sync_runner_run_until_stopped_processes_late_message_and_stops_gracefully() -> None:
    queue = InMemoryQueue()
    registry = InMemoryConsumerRegistry({int: ["sink"]})
    routing = RoutingService(registry=registry, strict=True)
    context_service = InMemoryKvContextService(InMemoryKvStore())
    seen: list[int] = []
    runner_ref: list[SyncRunner] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
            runner_ref[0].request_stop()
        return []

    runner = SyncRunner(
        nodes={"sink": sink},
        work_queue=queue,
        context_service=context_service,
        router=routing,
        observability=NoOpObservabilityService(),
    )
    runner_ref.append(runner)

    thread = threading.Thread(
        target=lambda: runner.run_until_stopped(poll_timeout_seconds=0.01, idle_timeout_seconds=1.0),
        daemon=True,
    )
    thread.start()
    time.sleep(0.02)
    enqueue_runner_input_sync(runner, 7, run_id="r", scenario_id="s", index=1)
    thread.join(timeout=1.0)

    assert seen == [7]
    assert thread.is_alive() is False


def test_sync_runner_run_until_stopped_returns_on_idle_timeout() -> None:
    runner = _build_sync_runner()
    started = time.monotonic()
    runner.run_until_stopped(poll_timeout_seconds=0.01, idle_timeout_seconds=0.05)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.03


def test_sync_runner_does_not_call_node_initialize_implicitly() -> None:
    class _Node:
        def __init__(self) -> None:
            self.init_calls = 0
            self.call_calls = 0

        def initialize(self) -> None:
            self.init_calls += 1

        def __call__(self, payload: object, _ctx: dict[str, object]) -> list[object]:
            _ = payload
            self.call_calls += 1
            return []

    node = _Node()
    queue = InMemoryQueue()
    queue.push(Envelope(payload="a", target="n", trace_id="t1"))
    queue.push(Envelope(payload="b", target="n", trace_id="t2"))
    runner = SyncRunner(
        nodes={"n": node},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
    )

    runner.run()

    assert node.init_calls == 0
    assert node.call_calls == 2


def test_runner_ingress_keeps_source_bootstrap_trace_empty_and_runner_generates_source_trace() -> None:
    class _Obs:
        def __init__(self) -> None:
            self.before: list[tuple[str, str | None]] = []

        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> None:
            _ = (payload, ctx)
            self.before.append((node_name, trace_id))
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
        ) -> None:
            _ = (node_name, payload, ctx, trace_id, outputs, state)
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

        def on_ingress(self, *, trace_id: str | None, reply_to: str | None) -> None:
            _ = (trace_id, reply_to)

        def on_terminal_event(self, *, trace_id: str | None, terminal_event: object | None) -> None:
            _ = (trace_id, terminal_event)

    queue = InMemoryQueue()
    registry = InMemoryConsumerRegistry({int: ["sink"]})
    routing = RoutingService(registry=registry, strict=True)
    obs = _Obs()
    runner = SyncRunner(
        nodes={
            "source:events": (lambda _payload, _ctx: [1]),
            "sink": (lambda _payload, _ctx: []),
        },
        run_id="run",
        scenario_id="scenario",
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=routing,
        observability=obs,
    )
    enqueue_runner_input_sync(
        runner,
        Envelope(payload=BootstrapControl(target="source:events"), target="source:events"),
        run_id="run",
        scenario_id="scenario",
        index=1,
    )
    runner.run()
    assert ("source:events", None) in obs.before
    assert ("sink", "run:events:1") in obs.before


def test_sync_runner_source_bootstrap_trace_does_not_collapse_generated_business_outputs() -> None:
    class _Obs(NoOpObservabilityService):
        def __init__(self) -> None:
            self.sink_trace_ids: list[str] = []

        def before_node(
            self,
            *,
            node_name: str,
            payload: object,
            ctx: dict[str, object],
            trace_id: str | None,
        ) -> None:
            _ = (payload, ctx)
            if node_name == "sink" and isinstance(trace_id, str) and trace_id:
                self.sink_trace_ids.append(trace_id)

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

    queue = InMemoryQueue()
    routing = RoutingService(registry=InMemoryConsumerRegistry({int: ["sink"]}), strict=True)
    obs = _Obs()
    runner = SyncRunner(
        nodes={
            "source:events": (lambda _payload, _ctx: [1, 2]),
            "sink": (lambda _payload, _ctx: []),
        },
        run_id="run",
        scenario_id="scenario",
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=routing,
        observability=obs,
    )
    queue.push(
        Envelope(
            payload=BootstrapControl(target="source:events"),
            target="source:events",
            trace_id="bootstrap-trace",
        )
    )

    runner.run()

    assert len(obs.sink_trace_ids) == 2
    assert len(set(obs.sink_trace_ids)) == 2
    assert all(trace_id != "bootstrap-trace" for trace_id in obs.sink_trace_ids)


def test_inmemory_kv_context_service_implements_context_service_contract() -> None:
    # SyncRunner depends on service contract, not storage adapter lifecycle.
    assert isinstance(InMemoryKvContextService(InMemoryKvStore()), ContextService)


def test_sync_runner_works_with_custom_queue_port_implementation() -> None:
    # Runner must depend on QueuePort contract, not on InMemoryQueue concrete type.
    class SpyQueue(QueuePort):
        def __init__(self) -> None:
            self.items: list[object] = []
            self.pushed: list[object] = []

        def push(self, envelope: object) -> None:
            self.pushed.append(envelope)
            self.items.append(envelope)

        def pop(self) -> object | None:
            if not self.items:
                return None
            return self.items.pop(0)

        def size(self) -> int:
            return len(self.items)

    queue = SpyQueue()
    registry = InMemoryConsumerRegistry({int: ["sink"]})
    routing = RoutingService(registry=registry, strict=True)
    context_service = InMemoryKvContextService(InMemoryKvStore())
    seen: list[int] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
        return []

    runner = SyncRunner(
        nodes={"sink": sink},
        work_queue=queue,
        context_service=context_service,
        router=routing,
        observability=NoOpObservabilityService(),
    )
    enqueue_runner_input_sync(runner, 7, run_id="r", scenario_id="s", index=1)
    runner.run()

    assert seen == [7]
    assert any(isinstance(item, Envelope) for item in queue.pushed)


def test_sync_runner_resolves_queue_and_routing_from_di() -> None:
    # Runner should not require manual queue/routing construction when DI bindings are available.
    queue = InMemoryQueue()
    registry = InMemoryConsumerRegistry({int: ["sink"]})
    routing = RoutingService(registry=registry, strict=True)
    context_service = InMemoryKvContextService(InMemoryKvStore())
    seen: list[int] = []

    def sink(payload: object, _ctx: dict[str, object]) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
        return []

    runner = SyncRunner(nodes={"sink": sink})
    di = InjectionRegistry()
    di.register_factory("queue", Envelope, lambda _q=queue: _q, qualifier="execution.cpu")
    di.register_factory("service", RoutingService, lambda _r=routing: _r)
    di.register_factory("service", ContextService, lambda _c=context_service: _c)
    di.register_factory("service", ObservabilityService, NoOpObservabilityService)
    scope = di.instantiate_for_scenario("s1")
    apply_injection(runner, scope, strict=True)

    enqueue_runner_input_sync(runner, 11, run_id="r", scenario_id="s", index=1)
    runner.run()
    assert seen == [11]


def test_sync_runner_collects_terminal_outputs_in_boundary_mode() -> None:
    # RUN-UNI-B1: boundary mode should collect terminal outputs as envelopes.
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1", reply_to="http:r1"))
    terminal_outputs: list[Envelope] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [TerminalEvent(status="success", payload={"ok": True})]

    runner = SyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        terminal_outputs=terminal_outputs,
    )
    runner.run()

    assert len(terminal_outputs) == 1
    assert terminal_outputs[0].trace_id == "t1"
    assert terminal_outputs[0].reply_to == "http:r1"
    assert terminal_outputs[0].payload == TerminalEvent(status="success", payload={"ok": True})


def test_sync_runner_collects_external_deliveries_for_unknown_local_targets() -> None:
    # RUN-UNI-B2: boundary mode should route out-of-group targets to external deliveries instead of crashing.
    class Event:
        pass

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1"))
    external_deliveries: list[Envelope] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [Event()]

    runner = SyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({Event: ["remote.node"]}), strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        external_deliveries=external_deliveries,
    )
    runner.run()

    assert len(external_deliveries) == 1
    assert external_deliveries[0].target == "remote.node"
    assert external_deliveries[0].trace_id == "t1"
    assert isinstance(external_deliveries[0].payload, Event)


def test_sync_runner_default_mode_still_fails_on_unknown_targets() -> None:
    # RUN-UNI-B3: default strict behavior remains unchanged when boundary mode is disabled.
    class Event:
        pass

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1"))

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [Event()]

    runner = SyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({Event: ["remote.node"]}), strict=True),
        observability=NoOpObservabilityService(),
    )
    with pytest.raises(ValueError, match="Unknown node"):
        runner.run()


def test_sync_runner_boundary_mode_collects_explicit_targeted_envelope_outputs() -> None:
    # RUN-UNI-B6: explicit Envelope(target=...) outputs should be forwarded via boundary channel in boundary mode.
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1", reply_to="http:r1"))
    external_deliveries: list[Envelope] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [Envelope(payload={"ok": True}, target="remote.node")]

    runner = SyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        external_deliveries=external_deliveries,
    )
    runner.run()

    assert len(external_deliveries) == 1
    assert external_deliveries[0].target == "remote.node"
    assert external_deliveries[0].trace_id == "t1"
    assert external_deliveries[0].reply_to == "http:r1"
    assert external_deliveries[0].payload == {"ok": True}


def test_sync_runner_boundary_mode_treats_no_consumer_output_as_terminal() -> None:
    # RUN-UNI-B4: in boundary mode, unroutable plain outputs are collected as terminal outputs.
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1"))
    terminal_outputs: list[Envelope] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return ["orphan-value"]

    runner = SyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({}), strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        terminal_outputs=terminal_outputs,
    )
    runner.run()

    assert len(terminal_outputs) == 1
    assert terminal_outputs[0].payload == "orphan-value"
    assert terminal_outputs[0].trace_id == "t1"


def test_sync_runner_boundary_mode_collects_terminal_for_unroutable_outputs() -> None:
    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1"))
    terminal_outputs: list[Envelope] = []
    held: list[ControlPlaneDeferredMessageHoldEvent] = []

    def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return ["orphan-value"]

    def hold_node(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = ctx
        if isinstance(payload, ControlPlaneDeferredMessageHoldEvent):
            held.append(payload)
        return []

    registry = InMemoryConsumerRegistry(
        {
            ControlPlaneDeferredMessageHoldEvent: ["system.cp.deferred_message_hold"],
        }
    )
    runner = SyncRunner(
        nodes={
            "n1": n1,
            "system.cp.deferred_message_hold": hold_node,
        },
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=registry, strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        terminal_outputs=terminal_outputs,
    )
    runner.run()

    assert held == []
    assert len(terminal_outputs) == 1
    assert terminal_outputs[0].payload == "orphan-value"
    assert terminal_outputs[0].trace_id == "t1"


def test_async_runner_collects_external_deliveries_for_unknown_local_targets() -> None:
    # RUN-UNI-B5: async runner must preserve boundary external-delivery semantics.
    class Event:
        pass

    queue = InMemoryQueue()
    queue.push(Envelope(payload="seed", target="n1", trace_id="t1"))
    external_deliveries: list[Envelope] = []

    async def n1(payload: object, ctx: dict[str, object]) -> list[object]:
        _ = (payload, ctx)
        return [Event()]

    runner = AsyncRunner(
        nodes={"n1": n1},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry({Event: ["remote.node"]}), strict=True),
        observability=NoOpObservabilityService(),
        allow_external_deliveries=True,
        external_deliveries=external_deliveries,
    )
    runner.run()

    assert len(external_deliveries) == 1
    assert external_deliveries[0].target == "remote.node"
    assert external_deliveries[0].trace_id == "t1"
