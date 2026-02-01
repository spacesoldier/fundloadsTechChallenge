from __future__ import annotations

from dataclasses import dataclass

# Runner semantics are documented in docs/implementation/kernel/Runner (Orchestrator) Spec.md.
from stream_kernel.kernel.context import ContextFactory
from stream_kernel.kernel.runner import Runner
from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.kernel.trace import TraceRecorder


@dataclass(frozen=True, slots=True)
class _Input:
    value: int


def test_runner_deterministic_order() -> None:
    # Runner must process each input end-to-end before the next (depth-first).
    outputs: list[int] = []

    def step1(msg, ctx):
        # Steps receive the raw message; use its fields explicitly.
        return [msg.value + 1]

    def step2(msg, ctx):
        outputs.append(msg)
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[
            StepSpec(name="s1", step=step1),
            StepSpec(name="s2", step=step2),
        ],
    )
    runner = Runner(scenario=scenario, context_factory=ContextFactory("run", "test"))
    runner.run([_Input(1), _Input(2)], output_sink=lambda _: None)
    assert outputs == [2, 3]


def test_runner_fanout_ordering() -> None:
    # Fan-out order must be preserved in subsequent steps.
    outputs: list[int] = []

    def fanout(msg, ctx):
        return [msg.value, msg.value + 1]

    def collect(msg, ctx):
        outputs.append(msg)
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="fanout", step=fanout), StepSpec(name="collect", step=collect)],
    )
    runner = Runner(scenario=scenario, context_factory=ContextFactory("run", "test"))
    runner.run([_Input(1)], output_sink=lambda _: None)
    assert outputs == [1, 2]


def test_runner_drop_semantics() -> None:
    # If a step drops a message (returns empty), later steps are skipped for that input.
    outputs: list[int] = []

    def drop(msg, ctx):
        return []

    def collect(msg, ctx):
        outputs.append(msg)
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="drop", step=drop), StepSpec(name="collect", step=collect)],
    )
    runner = Runner(scenario=scenario, context_factory=ContextFactory("run", "test"))
    runner.run([_Input(1)], output_sink=lambda _: None)
    assert outputs == []


def test_runner_exception_records_error() -> None:
    # Runner should record step exceptions in context and continue policy (fail fast here).
    errors: list[str] = []

    def boom(msg, ctx):
        raise ValueError("boom")

    def collect(msg, ctx):
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="boom", step=boom), StepSpec(name="collect", step=collect)],
    )
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "test"),
        on_error=lambda ctx, exc: errors.append(str(exc)),
    )
    runner.run([_Input(1)], output_sink=lambda _: None)
    assert errors == ["boom"]


def test_runner_tracing_records_per_step() -> None:
    # Tracing must record one TraceRecord per step invocation (Trace spec §10.3).
    ctxs = []

    def step1(msg, ctx):
        if ctx not in ctxs:
            ctxs.append(ctx)
        return [msg.value + 1]

    def step2(msg, ctx):
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="s1", step=step1), StepSpec(name="s2", step=step2)],
    )
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "test"),
        trace_recorder=recorder,
    )
    runner.run([_Input(1)], output_sink=lambda _: None)

    assert len(ctxs) == 1
    trace = ctxs[0].trace
    assert len(trace) == 2
    assert trace[0].step_name == "s1"
    assert trace[1].step_name == "s2"
    assert trace[0].step_index == 0
    assert trace[1].step_index == 1
    assert trace[0].trace_id == trace[1].trace_id


def test_runner_tracing_preserves_fanout_work_order() -> None:
    # Fan-out work_index ordering must be preserved in trace (Trace spec §11.2).
    ctxs = []

    def fanout(msg, ctx):
        if ctx not in ctxs:
            ctxs.append(ctx)
        return [msg.value, msg.value + 1]

    def collect(msg, ctx):
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="fanout", step=fanout), StepSpec(name="collect", step=collect)],
    )
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "test"),
        trace_recorder=recorder,
    )
    runner.run([_Input(1)], output_sink=lambda _: None)

    trace = ctxs[0].trace
    assert len(trace) == 3
    assert trace[0].step_name == "fanout"
    assert trace[1].step_name == "collect"
    assert trace[2].step_name == "collect"
    assert trace[1].work_index == 0
    assert trace[2].work_index == 1


def test_runner_tracing_drop_records_only_current_step() -> None:
    # Drop semantics still emit a record for the drop step (Trace spec §11.3).
    ctxs = []

    def drop(msg, ctx):
        if ctx not in ctxs:
            ctxs.append(ctx)
        return []

    def collect(msg, ctx):
        return [msg]

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="drop", step=drop), StepSpec(name="collect", step=collect)],
    )
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "test"),
        trace_recorder=recorder,
    )
    runner.run([_Input(1)], output_sink=lambda _: None)

    trace = ctxs[0].trace
    assert len(trace) == 1
    assert trace[0].step_name == "drop"


def test_runner_tracing_records_error_status() -> None:
    # Step exceptions must produce error records (Trace spec §11.7).
    ctxs = []
    errors: list[str] = []

    def boom(msg, ctx):
        if ctx not in ctxs:
            ctxs.append(ctx)
        raise ValueError("boom")

    scenario = Scenario(
        scenario_id="test",
        steps=[StepSpec(name="boom", step=boom)],
    )
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "test"),
        trace_recorder=recorder,
        on_error=lambda ctx, exc: errors.append(str(exc)),
    )
    runner.run([_Input(1)], output_sink=lambda _: None)

    trace = ctxs[0].trace
    assert len(trace) == 1
    assert trace[0].status == "error"
    assert trace[0].error is not None
    assert errors == ["boom"]
