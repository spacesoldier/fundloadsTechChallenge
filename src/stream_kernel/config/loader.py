from __future__ import annotations

from pathlib import Path

import yaml

from stream_kernel.config.validator import ConfigError


def load_yaml_config(path: Path) -> dict[str, object]:
    # Framework-level YAML loader; returns a raw mapping for validation.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")
    return raw
