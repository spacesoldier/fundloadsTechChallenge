from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from fund_load.usecases.config_models import AppConfig

# ConfigError is raised for invalid configuration (Configuration spec: fail fast).
class ConfigError(ValueError):
    pass


def load_config(path: Path) -> AppConfig:
    # YAML loader for configuration files (docs/implementation/architecture/Configuration spec.md).
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        # Wrap pydantic errors as ConfigError for a stable public API.
        raise ConfigError(str(exc)) from exc
