from __future__ import annotations

import types
from dataclasses import dataclass, field

# Full-flow integration is defined across:
# docs/framework/initial_stage/DAG construction.md
# docs/framework/initial_stage/Execution runtime and routing integration.md
from stream_kernel.application_context import ApplicationContext
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.context_store import InMemoryContextStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.kernel.node import node
from stream_kernel.routing.envelope import Envelope


class X:
    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"X({self.value})"


class Y:
    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"Y({self.value})"


def _make_module() -> types.ModuleType:
    mod = types.ModuleType("full_flow_nodes")

    @node(name="source", consumes=[], emits=[X])
    @dataclass(frozen=True, slots=True)
    class Source:
        def __call__(self, msg: object, ctx: object | None) -> list[object]:
            return [X("x")]

    @node(name="transform", consumes=[X], emits=[Y])
    @dataclass(frozen=True, slots=True)
    class Transform:
        seen: list[dict[str, object]] = field(default_factory=list)

        def __call__(self, msg: object, ctx: dict[str, object]) -> list[object]:
            self.seen.append(dict(ctx))
            return [Y("y")]

    @node(name="sink", consumes=[Y], emits=[])
    @dataclass(frozen=True, slots=True)
    class Sink:
        seen: list[dict[str, object]] = field(default_factory=list)
        payloads: list[object] = field(default_factory=list)

        def __call__(self, msg: object, ctx: dict[str, object]) -> list[object]:
            self.seen.append(dict(ctx))
            self.payloads.append(msg)
            return []

    mod.Source = Source
    mod.Transform = Transform
    mod.Sink = Sink
    return mod


def test_full_flow_runner_routing_with_dag_builder() -> None:
    ctx = ApplicationContext()
    ctx.discover([_make_module()])

    # Build analytic DAG from discovery (DAG construction §3–§4).
    dag = ctx.build_dag()
    assert dag.edges == [("source", "transform"), ("transform", "sink")]

    # Build routing registry and scenario instances.
    registry = ctx.build_consumer_registry()
    routing = RoutingPort(registry=registry, strict=True)

    scenario = ctx.build_scenario(
        scenario_id="s1",
        step_names=["source", "transform", "sink"],
        wiring={"strict": True},
    )
    nodes = {spec.name: spec.step for spec in scenario.steps}

    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    context_store.put("t1", {"trace": "ok"})

    work_queue.push(Envelope(payload="seed", target="source", trace_id="t1"))
    runner = SyncRunner(
        nodes=nodes,
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing,
    )
    runner.run()

    transform = nodes["transform"]
    sink = nodes["sink"]
    assert transform.seen == [{"trace": "ok"}]
    assert sink.seen == [{"trace": "ok"}]
    assert len(sink.payloads) == 1
    assert isinstance(sink.payloads[0], Y)
    assert sink.payloads[0].value == "y"
