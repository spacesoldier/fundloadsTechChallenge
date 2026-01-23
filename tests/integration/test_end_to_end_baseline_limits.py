from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# End-to-end baseline scenario validation is required by docs/Developer instructions.md.
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.config.loader import load_config
from fund_load.domain.messages import RawLine
from fund_load.domain.reasons import ReasonCode
from fund_load.kernel.context import Context, ContextFactory
from fund_load.kernel.runner import Runner
from fund_load.kernel.scenario_builder import ScenarioBuilder
from fund_load.kernel.trace import TraceRecorder
from fund_load.usecases.messages import Decision
from fund_load.usecases.wiring import build_step_registry


@dataclass(frozen=True, slots=True)
class _CollectingOutputSink:
    # OutputSink stub collects lines; WriteOutput expects write_line/close (OutputSink spec).
    lines: list[str]

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        pass


class _CollectingContextFactory:
    # ContextFactory wrapper to retain contexts for trace inspection (Trace spec §10.3).
    def __init__(self, run_id: str, scenario_id: str) -> None:
        self._factory = ContextFactory(run_id, scenario_id)
        self.contexts: list[Context] = []

    def new(self, *, line_no: int | None) -> Context:
        ctx = self._factory.new(line_no=line_no)
        self.contexts.append(ctx)
        return ctx


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _raw_line(line_no: int, *, id_value: str, customer_id: str, amount: str, ts: str) -> RawLine:
    # NDJSON line structure is defined in docs/Challenge task.md.
    payload = {
        "id": id_value,
        "customer_id": customer_id,
        "load_amount": amount,
        "time": ts,
    }
    return RawLine(line_no=line_no, raw_text=json.dumps(payload))


def test_baseline_end_to_end_limits_and_trace() -> None:
    # This test uses the baseline config and runs the full pipeline.
    # Output JSON does not include reason codes, so we insert a test-only "record_decisions" step
    # after EvaluatePolicies to capture Decision objects for assertions.
    config = load_config(_repo_root() / "src" / "fund_load" / "baseline_config.yml")

    decisions: list[Decision] = []

    def record_decisions(msg: Decision, ctx: Context | None) -> list[Decision]:
        decisions.append(msg)
        return [msg]

    wiring = {
        "prime_checker": SievePrimeChecker.from_max(0),
        "window_store": InMemoryWindowStore(),
        "output_sink": _CollectingOutputSink([]),
    }
    registry = build_step_registry(config, wiring=wiring)
    registry.register("record_decisions", lambda cfg, w: record_decisions)

    steps_cfg: list[dict[str, object]] = []
    for step in config.pipeline.steps:
        steps_cfg.append({"name": step.name, "config": step.config})
        if step.name == "evaluate_policies":
            steps_cfg.append({"name": "record_decisions", "config": {}})

    scenario = ScenarioBuilder(registry).build(
        scenario_id=config.scenario.name,
        steps=steps_cfg,
        wiring=wiring,
    )

    ctx_factory = _CollectingContextFactory("run", config.scenario.name)
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    runner = Runner(
        scenario=scenario,
        context_factory=ctx_factory,
        trace_recorder=recorder,
    )

    inputs = [
        # Customer 101: daily attempt limit on 4th attempt (limits are per customer/day).
        _raw_line(1, id_value="1001", customer_id="101", amount="USD100.00", ts="2025-01-01T10:00:00Z"),
        _raw_line(2, id_value="1002", customer_id="101", amount="USD100.00", ts="2025-01-01T11:00:00Z"),
        _raw_line(3, id_value="1003", customer_id="101", amount="USD100.00", ts="2025-01-01T12:00:00Z"),
        _raw_line(4, id_value="1004", customer_id="101", amount="USD100.00", ts="2025-01-01T13:00:00Z"),
        # Customer 202: daily amount limit exceeded on 2nd attempt.
        _raw_line(5, id_value="2001", customer_id="202", amount="$3000.00", ts="2025-01-02T10:00:00Z"),
        _raw_line(6, id_value="2002", customer_id="202", amount="$2500.00", ts="2025-01-02T11:00:00Z"),
        # Customer 303: weekly limit exceeded on 5th day in the same week.
        _raw_line(7, id_value="3001", customer_id="303", amount="USD5000.00", ts="2025-01-06T10:00:00Z"),
        _raw_line(8, id_value="3002", customer_id="303", amount="USD5000.00", ts="2025-01-07T10:00:00Z"),
        _raw_line(9, id_value="3003", customer_id="303", amount="USD5000.00", ts="2025-01-08T10:00:00Z"),
        _raw_line(10, id_value="3004", customer_id="303", amount="USD5000.00", ts="2025-01-09T10:00:00Z"),
        _raw_line(11, id_value="3005", customer_id="303", amount="USD1000.00", ts="2025-01-10T10:00:00Z"),
    ]

    runner.run(inputs, output_sink=lambda _: None)

    decision_by_id = {d.id: d for d in decisions}
    assert decision_by_id["1001"].accepted is True
    assert decision_by_id["1002"].accepted is True
    assert decision_by_id["1003"].accepted is True
    assert decision_by_id["1004"].accepted is False
    assert decision_by_id["1004"].reasons == (ReasonCode.DAILY_ATTEMPT_LIMIT.value,)

    assert decision_by_id["2001"].accepted is True
    assert decision_by_id["2002"].accepted is False
    assert decision_by_id["2002"].reasons == (ReasonCode.DAILY_AMOUNT_LIMIT.value,)

    assert decision_by_id["3001"].accepted is True
    assert decision_by_id["3002"].accepted is True
    assert decision_by_id["3003"].accepted is True
    assert decision_by_id["3004"].accepted is True
    assert decision_by_id["3005"].accepted is False
    assert decision_by_id["3005"].reasons == (ReasonCode.WEEKLY_AMOUNT_LIMIT.value,)

    # Trace order: each input has one record per step, in order (Trace spec §5).
    expected_steps = [step["name"] for step in steps_cfg]
    ctx_by_line = {ctx.line_no: ctx for ctx in ctx_factory.contexts}
    assert ctx_by_line[1].line_no == 1
    for ctx in ctx_factory.contexts:
        step_names = [rec.step_name for rec in ctx.trace]
        assert step_names == expected_steps
