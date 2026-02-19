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


def test_multiprocess_supervisor_wait_output_closed_called_before_stop() -> None:
    # D4: lifecycle orchestration must call wait_output_closed on the supervisor
    # before stop_groups, so the child has a chance to flush outputs.
    from stream_kernel.application_context.injection_registry import InjectionRegistry
    from stream_kernel.execution.orchestration.lifecycle_orchestration import (
        execute_with_bootstrap_supervisor,
    )
    from stream_kernel.platform.services.runtime.bootstrap import BootstrapSupervisor
    from stream_kernel.routing.router import RoutingResult

    calls: list[str] = []

    class _TrackingSupervisor(BootstrapSupervisor):
        def start_groups(self, group_names: list[str]) -> None:
            calls.append("start_groups")

        def wait_ready(self, timeout_seconds: int) -> bool:
            return True

        def execute_boundary(
            self,
            *,
            run: object,
            run_id: str,
            scenario_id: str,
            inputs: list[object],
        ) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            calls.append("stop_groups")

        def wait_output_closed(self, timeout_seconds: int) -> bool:
            # Signal: all child output is flushed.
            calls.append("wait_output_closed")
            return True

    supervisor = _TrackingSupervisor()
    di = InjectionRegistry()
    di.register_factory("service", BootstrapSupervisor, lambda _s=supervisor: _s)
    scope = di.instantiate_for_scenario("d4-test")

    runtime = {
        "platform": {
            "process_groups": [{"name": "workers"}],
            "lifecycle": {
                "ready_timeout_seconds": 5,
                "graceful_timeout_seconds": 10,
                "drain_inflight": True,
            },
        },
    }

    execute_with_bootstrap_supervisor(
        config={},
        runtime=runtime,
        scenario_id="d4-test",
        run_id="run1",
        inputs=[],
        scenario_scope=scope,
        run=lambda: None,
    )

    # wait_output_closed must be called before stop_groups.
    assert "wait_output_closed" in calls, (
        "lifecycle orchestration must call wait_output_closed on supervisor"
    )
    assert "stop_groups" in calls
    woc_idx = calls.index("wait_output_closed")
    stop_idx = calls.index("stop_groups")
    assert woc_idx < stop_idx, (
        f"wait_output_closed (pos {woc_idx}) must precede stop_groups (pos {stop_idx})"
    )
