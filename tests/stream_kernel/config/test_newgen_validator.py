from __future__ import annotations

import pytest

from stream_kernel.config.validator import ConfigError, validate_newgen_config


def test_validate_newgen_config_happy_path() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"strict": True, "discovery_modules": ["example.steps"]},
        "nodes": {
            "compute_features": {
                "monday_multiplier": {"enabled": False, "multiplier": 2.0, "apply_to": "amount"},
                "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": 9999.0},
            },
            "evaluate_policies": {
                "limits": {"daily_amount": 5000.0, "weekly_amount": 20000.0, "daily_attempts": 3},
                "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": 9999.0},
            },
            "update_windows": {
                "daily_attempts": {"enabled": True},
                "daily_accepted_amount": {"enabled": True},
                "weekly_accepted_amount": {"enabled": True},
                "daily_prime_gate": {"enabled": False},
            },
        },
        "adapters": {
            "output_sink": {
                "factory": "example.adapters:output_sink",
                "settings": {"path": "output.txt"},
                "binds": [{"port_type": "stream", "type": "example.ports:OutputSink"}],
            }
        },
    }

    validated = validate_newgen_config(raw)
    assert validated["version"] == 1
    assert validated["scenario"]["name"] == "baseline"
    assert validated["runtime"]["strict"] is True
    assert "nodes" in validated
    assert "adapters" in validated


@pytest.mark.parametrize("bad_root", [None, [], "nope"])
def test_validate_newgen_config_requires_mapping(bad_root: object) -> None:
    with pytest.raises(ConfigError):
        validate_newgen_config(bad_root)  # type: ignore[arg-type]


def test_validate_newgen_config_requires_scenario_name() -> None:
    raw = {"version": 1, "scenario": {}, "nodes": {}, "adapters": {}}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_output_sink_mapping() -> None:
    raw = {"version": 1, "scenario": {"name": "baseline"}, "nodes": {}, "adapters": {"output_sink": "x"}}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_discovery_modules_list() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": "not-a-list"},
        "nodes": {},
        "adapters": {"output_sink": {"factory": "x", "binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_adapter_factory() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_adapter_binds_list() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"factory": "x", "binds": "nope"}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)
