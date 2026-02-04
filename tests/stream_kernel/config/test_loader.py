from __future__ import annotations

from pathlib import Path

import pytest

from stream_kernel.config.loader import ConfigError, load_yaml_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_yaml_config_returns_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 1\nscenario:\n  name: baseline\n")
    data = load_yaml_config(path)
    assert data["version"] == 1
    assert data["scenario"]["name"] == "baseline"


def test_load_yaml_config_rejects_non_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "[]\n")
    with pytest.raises(ConfigError):
        load_yaml_config(path)
