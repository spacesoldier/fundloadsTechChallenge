from __future__ import annotations

from pathlib import Path

import pytest

# Config loading rules are specified in docs/implementation/architecture/Configuration spec.md.
from fund_load.config.loader import ConfigError, load_config


def test_load_config_happy_path(tmp_path: Path) -> None:
    # Minimal config should load with required fields present.
    path = tmp_path / "config.yml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "pipeline:",
                "  steps:",
                "    - name: parse_load_attempt",
                "scenario:",
                "  name: baseline",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg["version"] == 1
    assert cfg["scenario"]["name"] == "baseline"
    assert cfg["pipeline"]["steps"][0]["name"] == "parse_load_attempt"


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
