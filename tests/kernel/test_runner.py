from __future__ import annotations

from dataclasses import dataclass

# Runner semantics are documented in docs/implementation/kernel/Runner (Orchestrator) Spec.md.
from fund_load.kernel.context import ContextFactory
from fund_load.kernel.runner import Runner
from fund_load.kernel.scenario import Scenario, StepSpec


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
