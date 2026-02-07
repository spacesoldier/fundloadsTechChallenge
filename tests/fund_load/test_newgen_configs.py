from __future__ import annotations

from pathlib import Path

from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config


BASELINE = Path("src/fund_load/baseline_config_newgen.yml")
EXPERIMENT = Path("src/fund_load/experiment_config_newgen.yml")


def _load_validated(path: Path) -> dict[str, object]:
    # Framework should accept shipped newgen configs as-is.
    return validate_newgen_config(load_yaml_config(path))


def test_baseline_newgen_config_is_runtime_compatible() -> None:
    # runtime.pipeline was deprecated; shipped baseline config must not rely on it.
    cfg = _load_validated(BASELINE)
    runtime = cfg.get("runtime", {})
    assert isinstance(runtime, dict)
    assert "pipeline" not in runtime


def test_experiment_newgen_config_is_runtime_compatible() -> None:
    # Experimental config follows the same runtime contract as baseline config.
    cfg = _load_validated(EXPERIMENT)
    runtime = cfg.get("runtime", {})
    assert isinstance(runtime, dict)
    assert "pipeline" not in runtime
