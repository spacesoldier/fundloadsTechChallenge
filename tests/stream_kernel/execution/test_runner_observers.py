from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.platform.services.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.routing.envelope import Envelope


@dataclass
class _Observer:
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

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
        return None


def _routing() -> RoutingPort:
    return RoutingPort(registry=InMemoryConsumerRegistry(), strict=True)


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
