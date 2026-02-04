from __future__ import annotations

from pathlib import Path

import pytest

# Composition root wiring is documented in docs/implementation/kernel/Composition Root Spec.md.
from stream_kernel.kernel.composition_root import build_runtime


def test_composition_root_builds_runtime(tmp_path: Path) -> None:
    # Minimal config should build a runtime with runner and scenario.
    config = {
        "scenario_id": "baseline",
        "steps": [
            {"name": "noop"},
        ],
    }
    # Minimal wiring for ports; concrete adapters are not required for this smoke test.
    runtime = build_runtime(config=config, wiring={"steps": {"noop": lambda cfg, w: lambda msg, ctx: [msg]}})
    assert runtime.runner is not None
    assert runtime.scenario is not None
    assert runtime.scenario.scenario_id == "baseline"


def test_composition_root_unknown_step_fails() -> None:
    # Unknown step in config must fail fast.
    config = {
        "scenario_id": "baseline",
        "steps": [
            {"name": "missing"},
        ],
    }
    with pytest.raises(KeyError):
        build_runtime(config=config, wiring={"steps": {}})


def test_composition_root_requires_scenario_id_and_steps() -> None:
    # Composition root validates scenario_id + steps list (Composition Root spec).
    with pytest.raises(ValueError):
        build_runtime(config={"scenario_id": 1, "steps": "nope"}, wiring={})
