from __future__ import annotations

# Phase D: SyncRunner stop signal and multiprocess output-closed ack.
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.routing_service import RoutingService


def _make_runner(*, queue, drain_on_stop: bool = True):
    from stream_kernel.execution.runtime.runner import SyncRunner

    return SyncRunner(
        nodes={},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
        drain_on_stop=drain_on_stop,
    )


def test_sync_runner_has_drain_on_stop_and_request_stop() -> None:
    # D1: SyncRunner must mirror AsyncRunner stop interface.
    from stream_kernel.execution.runtime.runner import SyncRunner

    runner = SyncRunner(
        nodes={},
        work_queue=InMemoryQueue(),
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
    )
    # Fields present
    assert hasattr(runner, "drain_on_stop"), "SyncRunner must have drain_on_stop field"
    assert hasattr(runner, "_stop_requested"), "SyncRunner must have _stop_requested field"
    # Method present
    assert callable(getattr(runner, "request_stop", None)), "SyncRunner must have request_stop()"
    # Defaults
    assert runner.drain_on_stop is True
    assert runner._stop_requested is False


def test_sync_runner_respects_stop_between_iterations() -> None:
    # D2: drain_on_stop=False → runner exits after current item when stop is requested.
    seen: list[int] = []
    runner_ref: list[object] = []

    def node_a(payload: object, _ctx: dict) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
            if payload == 1:
                runner_ref[0].request_stop()
        return []

    from stream_kernel.execution.runtime.runner import SyncRunner

    queue = InMemoryQueue()
    queue.push(Envelope(payload=1, target="A", trace_id="t1"))
    queue.push(Envelope(payload=2, target="A", trace_id="t2"))
    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
        drain_on_stop=False,
    )
    runner_ref.append(runner)
    runner.run()

    # With drain_on_stop=False: item 1 is processed (stop requested during it),
    # item 2 is NOT processed.
    assert seen == [1], f"Expected [1] but got {seen}"


def test_sync_runner_drain_on_stop_empties_queue() -> None:
    # D3: drain_on_stop=True → runner processes all queued items even after stop is requested.
    seen: list[int] = []
    runner_ref: list[object] = []

    def node_a(payload: object, _ctx: dict) -> list[object]:
        if isinstance(payload, int):
            seen.append(payload)
            if payload == 1:
                runner_ref[0].request_stop()
        return []

    from stream_kernel.execution.runtime.runner import SyncRunner

    queue = InMemoryQueue()
    queue.push(Envelope(payload=1, target="A", trace_id="t1"))
    queue.push(Envelope(payload=2, target="A", trace_id="t2"))
    runner = SyncRunner(
        nodes={"A": node_a},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
        drain_on_stop=True,
    )
    runner_ref.append(runner)
    runner.run()

    # With drain_on_stop=True: both items processed, then runner exits cleanly.
    assert seen == [1, 2], f"Expected [1, 2] but got {seen}"
