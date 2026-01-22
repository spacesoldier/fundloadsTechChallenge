from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ConfigError is raised for invalid configuration (Configuration spec: fail fast).
class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StepConfig:
    # StepConfig is a lightweight representation used by usecases wiring.
    name: str
    params: dict[str, Any]


def load_config(path: Path) -> dict[str, Any]:
    # YAML loader for configuration files (docs/implementation/architecture/Configuration spec.md).
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    _validate_top_level(raw)
    return raw


def _validate_top_level(raw: dict[str, Any]) -> None:
    # Fail fast on unknown keys to prevent silent misconfiguration.
    allowed = {"version", "pipeline", "scenario", "domain", "idempotency", "features", "policies", "windows", "output"}
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise ConfigError(f"Unknown top-level keys: {sorted(unknown)}")

    if "version" not in raw or "pipeline" not in raw or "scenario" not in raw:
        raise ConfigError("Missing required top-level keys: version, pipeline, scenario")

    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict) or "steps" not in pipeline:
        raise ConfigError("pipeline.steps is required")

    steps = pipeline.get("steps")
    if not isinstance(steps, list):
        raise ConfigError("pipeline.steps must be a list")
