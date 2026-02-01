from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# End-to-end experimental scenario validation per docs/Developer instructions.md.
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.config.loader import load_config
from fund_load.domain.messages import RawLine
from fund_load.domain.reasons import ReasonCode
from stream_kernel.kernel.context import Context, ContextFactory
from stream_kernel.kernel.runner import Runner
from stream_kernel.kernel.scenario_builder import ScenarioBuilder
from stream_kernel.kernel.trace import TraceRecorder
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


def test_experiment_end_to_end_monday_multiplier_and_prime_gate() -> None:
    # Experimental config enables Monday multiplier and prime gate (docs/analysis/data/assets/output_exp_mp.txt).
    config = load_config(_repo_root() / "src" / "fund_load" / "experiment_config.yml")

    decisions: list[Decision] = []

    def record_decisions(msg: Decision, ctx: Context | None) -> list[Decision]:
        decisions.append(msg)
        return [msg]

    wiring = {
        "prime_checker": SievePrimeChecker.from_max(50000),
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
        # Monday multiplier doubles amount; 3000 -> 6000 exceeds daily limit (5000).
        _raw_line(1, id_value="40000", customer_id="401", amount="USD3000.00", ts="2025-01-06T09:00:00Z"),
        # Prime id (five-digit prime) accepted first time (prime gate global limit=1).
        _raw_line(2, id_value="10007", customer_id="402", amount="USD100.00", ts="2025-01-06T10:00:00Z"),
        # Second prime on same day hits PRIME_DAILY_GLOBAL_LIMIT.
        _raw_line(3, id_value="10009", customer_id="402", amount="USD100.00", ts="2025-01-06T11:00:00Z"),
    ]

    runner.run(inputs, output_sink=lambda _: None)

    decision_by_id = {d.id: d for d in decisions}
    monday = decision_by_id["40000"]
    assert monday.accepted is False
    assert monday.reasons == (ReasonCode.DAILY_AMOUNT_LIMIT.value,)
    assert monday.effective_amount.amount > Decimal("5000.00")
    assert monday.is_prime_id is False

    prime1 = decision_by_id["10007"]
    assert prime1.accepted is True
    assert prime1.reasons == ()
    assert prime1.is_prime_id is True

    prime2 = decision_by_id["10009"]
    assert prime2.accepted is False
    assert prime2.reasons == (ReasonCode.PRIME_DAILY_GLOBAL_LIMIT.value,)
    assert prime2.is_prime_id is True

    expected_steps = [step["name"] for step in steps_cfg]
    for ctx in ctx_factory.contexts:
        step_names = [rec.step_name for rec in ctx.trace]
        assert step_names == expected_steps
