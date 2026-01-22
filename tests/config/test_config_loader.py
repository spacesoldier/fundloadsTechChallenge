from __future__ import annotations

from pathlib import Path

import pytest

# Config loading rules are specified in docs/implementation/architecture/Configuration spec.md.
from fund_load.config.loader import ConfigError, load_config
from fund_load.usecases.config_models import AppConfig


def test_load_config_happy_path(tmp_path: Path) -> None:
    # Minimal config should load with required fields present.
    path = tmp_path / "config.yml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "scenario:",
                "  name: baseline",
                "pipeline:",
                "  steps:",
                "    - name: parse_load_attempt",
                "policies:",
                "  pack: baseline",
                "  evaluation_order:",
                "    - daily_attempt_limit",
                "    - daily_amount_limit",
                "    - weekly_amount_limit",
                "  limits:",
                "    daily_amount: 5000.00",
                "    weekly_amount: 20000.00",
                "    daily_attempts: 3",
                "features:",
                "  enabled: true",
                "  monday_multiplier:",
                "    enabled: false",
                "    multiplier: 2.0",
                "    apply_to: amount",
                "  prime_gate:",
                "    enabled: false",
                "    global_per_day: 1",
                "    amount_cap: 9999.00",
                "windows:",
                "  daily_attempts:",
                "    enabled: true",
                "  daily_accepted_amount:",
                "    enabled: true",
                "  weekly_accepted_amount:",
                "    enabled: true",
                "  daily_prime_gate:",
                "    enabled: false",
                "output:",
                "  file: output.txt",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)
    assert cfg.version == 1
    assert cfg.scenario.name == "baseline"
    assert cfg.pipeline.steps[0].name == "parse_load_attempt"


def test_load_config_unknown_top_level_key_fails(tmp_path: Path) -> None:
    # Unknown top-level keys are rejected (Configuration spec: fail fast).
    path = tmp_path / "config.yml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "pipeline:",
                "  steps: []",
                "scenario:",
                "  name: baseline",
                "policies:",
                "  pack: baseline",
                "  evaluation_order: []",
                "  limits:",
                "    daily_amount: 1.00",
                "    weekly_amount: 1.00",
                "    daily_attempts: 1",
                "features:",
                "  enabled: true",
                "  monday_multiplier:",
                "    enabled: false",
                "    multiplier: 2.0",
                "    apply_to: amount",
                "  prime_gate:",
                "    enabled: false",
                "    global_per_day: 1",
                "    amount_cap: 9999.00",
                "windows:",
                "  daily_attempts:",
                "    enabled: true",
                "  daily_accepted_amount:",
                "    enabled: true",
                "  weekly_accepted_amount:",
                "    enabled: true",
                "  daily_prime_gate:",
                "    enabled: false",
                "output:",
                "  file: output.txt",
                "unknown: 1",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_missing_required_key_fails(tmp_path: Path) -> None:
    # Missing required keys should raise a ConfigError.
    path = tmp_path / "config.yml"
    path.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
