from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

# Wiring rules are derived from Configuration spec + Step specs.
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.domain.messages import IdemStatus, LoadAttempt
from fund_load.domain.money import Money
from fund_load.kernel.scenario_builder import ScenarioBuilder
from fund_load.usecases.config_models import AppConfig
from fund_load.usecases.messages import (
    AttemptWithKeys,
    IdempotencyClassifiedAttempt,
    OutputLine,
    WeekKey,
)
from fund_load.usecases.wiring import build_step_registry


class _FakePrimeChecker:
    # Fake PrimeChecker for wiring tests; matches port shape.
    def is_prime(self, id_num: int) -> bool:
        return id_num == 11


class _FakeOutputSink:
    # Fake OutputSink that records lines for assertions.
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        pass


def _config(exp: bool = False) -> AppConfig:
    # Build a minimal AppConfig using the same shape as loader tests.
    data = {
        "version": 1,
        "scenario": {"name": "exp_mp" if exp else "baseline"},
        "pipeline": {
            "steps": [
                {"name": "parse_load_attempt"},
                {"name": "compute_time_keys"},
                {"name": "idempotency_gate"},
                {"name": "compute_features"},
                {"name": "evaluate_policies"},
                {"name": "update_windows"},
                {"name": "format_output"},
                {"name": "write_output"},
            ]
        },
        "policies": {
            "pack": "exp_mp" if exp else "baseline",
            "evaluation_order": [
                "daily_attempt_limit",
                "prime_global_gate",
                "daily_amount_limit",
                "weekly_amount_limit",
            ]
            if exp
            else [
                "daily_attempt_limit",
                "daily_amount_limit",
                "weekly_amount_limit",
            ],
            "limits": {
                "daily_amount": "5000.00",
                "weekly_amount": "20000.00",
                "daily_attempts": 3,
            },
            **(
                {
                    "prime_gate": {
                        "enabled": True,
                        "global_per_day": 1,
                        "amount_cap": "9999.00",
                    }
                }
                if exp
                else {}
            ),
        },
        "features": {
            "enabled": True,
            "monday_multiplier": {"enabled": exp, "multiplier": "2.0", "apply_to": "amount"},
            "prime_gate": {"enabled": exp, "global_per_day": 1, "amount_cap": "9999.00"},
        },
        "windows": {
            "daily_attempts": {"enabled": True},
            "daily_accepted_amount": {"enabled": True},
            "weekly_accepted_amount": {"enabled": True},
            "daily_prime_gate": {"enabled": exp},
        },
        "output": {"file": "output.txt"},
    }
    return AppConfig.model_validate(data)


def test_step_registry_builds_scenario_from_config() -> None:
    # ScenarioBuilder should build a scenario using pipeline step names from config.
    cfg = _config(exp=False)
    wiring = {
        "prime_checker": _FakePrimeChecker(),
        "window_store": InMemoryWindowStore(),
        "output_sink": _FakeOutputSink(),
    }
    registry = build_step_registry(cfg, wiring)
    scenario = ScenarioBuilder(registry).build(
        scenario_id=cfg.scenario.name,
        steps=[{"name": step.name, "config": step.params} for step in cfg.pipeline.steps],
        wiring=wiring,
    )
    assert [s.name for s in scenario.steps] == [
        "parse_load_attempt",
        "compute_time_keys",
        "idempotency_gate",
        "compute_features",
        "evaluate_policies",
        "update_windows",
        "format_output",
        "write_output",
    ]


def test_wiring_compute_features_uses_config() -> None:
    # Monday multiplier must reflect config values when building the step.
    cfg = _config(exp=True)
    wiring = {
        "prime_checker": _FakePrimeChecker(),
        "window_store": InMemoryWindowStore(),
        "output_sink": _FakeOutputSink(),
    }
    registry = build_step_registry(cfg, wiring)
    scenario = ScenarioBuilder(registry).build(
        scenario_id=cfg.scenario.name,
        steps=[{"name": step.name, "config": step.params} for step in cfg.pipeline.steps],
        wiring=wiring,
    )
    compute_features = next(s.step for s in scenario.steps if s.name == "compute_features")
    ts = datetime(2000, 1, 3, 12, 0, 0, tzinfo=UTC)  # Monday
    attempt = LoadAttempt(
        line_no=1,
        id="11",
        customer_id="20",
        amount=Money("USD", Decimal("10.00")),
        ts=ts,
        raw={},
    )
    day_key = ts.date()
    week_key = WeekKey(week_start_date=day_key, week_start="MON")
    with_keys = AttemptWithKeys(attempt=attempt, day_key=day_key, week_key=week_key)
    classified = IdempotencyClassifiedAttempt(
        base=with_keys,
        idem_status=IdemStatus.CANONICAL,
        fingerprint="fp",
        canonical_line_no=1,
    )
    enriched = list(compute_features(classified, ctx=None))[0]
    assert enriched.features.risk_factor == Decimal("2.0")


def test_wiring_write_output_uses_output_sink() -> None:
    # WriteOutput step must use the OutputSink provided in wiring.
    cfg = _config(exp=False)
    sink = _FakeOutputSink()
    wiring = {
        "prime_checker": _FakePrimeChecker(),
        "window_store": InMemoryWindowStore(),
        "output_sink": sink,
    }
    registry = build_step_registry(cfg, wiring)
    scenario = ScenarioBuilder(registry).build(
        scenario_id=cfg.scenario.name,
        steps=[{"name": step.name, "config": step.params} for step in cfg.pipeline.steps],
        wiring=wiring,
    )
    write_output = next(s.step for s in scenario.steps if s.name == "write_output")
    write_output(OutputLine(line_no=1, json_text='{"id":"1"}'), ctx=None)
    assert sink.lines == ['{"id":"1"}']
