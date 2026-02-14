from __future__ import annotations

from stream_kernel.execution.runtime.runner import SyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    legacy_reply_aware_observability,
)
from stream_kernel.platform.services.messaging.reply_waiter import (
    InMemoryReplyWaiterService,
    TerminalEvent,
)
from stream_kernel.routing.envelope import Envelope


def _runner(
    *,
    node_impl,
    context_service: InMemoryKvContextService,
    waiter: InMemoryReplyWaiterService,
) -> SyncRunner:
    routing = RoutingService(registry=InMemoryConsumerRegistry({}), strict=True)
    return SyncRunner(
        nodes={"n1": node_impl},
        work_queue=InMemoryQueue(),
        context_service=context_service,
        router=routing,
        observability=legacy_reply_aware_observability(
            inner=NoOpObservabilityService(),
            reply_waiter=waiter,
        ),
    )


def test_runner_registers_waiter_and_persists_reply_metadata_on_ingress() -> None:
    # Step-C wiring: ingress Envelope(reply_to=...) should register waiter and store __reply_to.
    store = InMemoryKvStore()
    context_service = InMemoryKvContextService(store)
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)

    def node(payload: object, _ctx: dict[str, object]) -> list[object]:
        _ = payload
        return []

    runner = _runner(node_impl=node, context_service=context_service, waiter=waiter)
    runner.run_inputs(
        [Envelope(payload="msg", target="n1", trace_id="t1", reply_to="http:req-1")],
        run_id="run",
        scenario_id="scenario",
    )

    assert waiter.in_flight() == 1
    assert context_service.metadata("t1", full=True).get("__reply_to") == "http:req-1"


def test_runner_completes_waiter_on_terminal_event_output() -> None:
    # Step-C wiring: terminal output should complete waiter deterministically by trace_id.
    store = InMemoryKvStore()
    context_service = InMemoryKvContextService(store)
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)

    def node(payload: object, _ctx: dict[str, object]) -> list[object]:
        _ = payload
        return [TerminalEvent(status="success", payload={"ok": True})]

    runner = _runner(node_impl=node, context_service=context_service, waiter=waiter)
    runner.run_inputs(
        [Envelope(payload="msg", target="n1", trace_id="t1", reply_to="http:req-1")],
        run_id="run",
        scenario_id="scenario",
    )

    assert waiter.in_flight() == 0
    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"ok": True})


def test_runner_uses_first_terminal_event_when_node_emits_duplicates() -> None:
    # Step-C wiring: duplicate terminal outputs should not overwrite first completion.
    store = InMemoryKvStore()
    context_service = InMemoryKvContextService(store)
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)

    def node(payload: object, _ctx: dict[str, object]) -> list[object]:
        _ = payload
        return [
            TerminalEvent(status="success", payload={"n": 1}),
            TerminalEvent(status="success", payload={"n": 2}),
        ]

    runner = _runner(node_impl=node, context_service=context_service, waiter=waiter)
    runner.run_inputs(
        [Envelope(payload="msg", target="n1", trace_id="t1", reply_to="http:req-1")],
        run_id="run",
        scenario_id="scenario",
    )

    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"n": 1})
