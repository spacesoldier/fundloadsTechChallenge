from __future__ import annotations

# Runner interface is part of the execution model (Execution runtime + planning docs).
from stream_kernel.platform.services.context import ContextService, InMemoryKvContextService
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.execution.runner_port import RunnerPort
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry


def _build_sync_runner() -> SyncRunner:
    # Minimal runner instance for interface checks.
    registry = InMemoryConsumerRegistry({})
    routing = RoutingPort(registry=registry, strict=True)
    context_service = InMemoryKvContextService(InMemoryKvStore())
    return SyncRunner(
        nodes={},
        work_queue=InMemoryWorkQueue(),
        context_service=context_service,
        routing_port=routing,
    )


def test_sync_runner_implements_runner_port() -> None:
    # SyncRunner should conform to the RunnerPort interface.
    runner = _build_sync_runner()
    assert isinstance(runner, RunnerPort)


def test_non_runner_does_not_match_port() -> None:
    # Objects without a run() method should not satisfy RunnerPort.
    class NotRunner:
        pass

    assert isinstance(NotRunner(), RunnerPort) is False


def test_runner_port_run_returns_none() -> None:
    # Runner.run() is a fire-and-forget operation with no return value.
    runner = _build_sync_runner()
    assert runner.run() is None


def test_inmemory_kv_context_service_implements_context_service_contract() -> None:
    # SyncRunner depends on service contract, not storage adapter lifecycle.
    assert isinstance(InMemoryKvContextService(InMemoryKvStore()), ContextService)
