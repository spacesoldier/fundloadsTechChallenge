from __future__ import annotations

# Runner interface is part of the execution model (Execution runtime + planning docs).
from stream_kernel.application_context.application_context import apply_injection
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.platform.services.state.context import ContextService, InMemoryKvContextService
from stream_kernel.platform.services.observability import (
    NoOpObservabilityService,
    ObservabilityService,
)
from stream_kernel.execution.runtime.runner import SyncRunner
from stream_kernel.execution.runtime.runner_port import RunnerPort
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.integration.work_queue import InMemoryQueue, QueuePort
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.routing.envelope import Envelope


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
    runner.run_inputs([7], run_id="r", scenario_id="s")

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

    runner.run_inputs([11], run_id="r", scenario_id="s")
    assert seen == [11]
