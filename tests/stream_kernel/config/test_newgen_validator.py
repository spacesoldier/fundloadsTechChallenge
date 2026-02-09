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
                "settings": {"path": "output.txt"},
                "binds": ["stream"],
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
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_output_sink_kind_field() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"kind": "file.line_writer", "binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_adapter_factory_field() -> None:
    # Factory paths are removed from config contract; adapters are resolved by kind via discovery/registry.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"factory": "x", "binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_adapter_binds_list() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": "nope"}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_runtime_mapping() -> None:
    # runtime must be a mapping if provided (Configuration spec §2.1).
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": "nope",
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_nodes_mapping() -> None:
    # nodes must be a mapping if provided (Configuration spec §2.1).
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "nodes": "nope",
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_adapters_mapping() -> None:
    # adapters must be a mapping if provided (Configuration spec §2.1).
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "adapters": "nope",
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_output_sink_settings_mapping() -> None:
    # output_sink.settings must be a mapping (Configuration spec §2.1).
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "adapters": {"output_sink": {"binds": [], "settings": "nope"}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_defensive_output_sink_type_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defensive check: output_sink must remain a mapping after helper (validator internal guard).
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "adapters": {"output_sink": {"binds": []}},
    }

    def _require_mapping(root: dict[str, object], key: str) -> dict[str, object]:
        if key == "scenario":
            return {"name": "baseline"}
        return "nope"  # type: ignore[return-value]

    monkeypatch.setattr("stream_kernel.config.validator._require_mapping", _require_mapping)

    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_non_string_bind_entry() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": [{"port_type": "stream"}]}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


@pytest.mark.parametrize("port_type", ["stream", "kv_stream", "kv", "request", "response", "service"])
def test_validate_newgen_config_accepts_stable_bind_port_types(port_type: str) -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": [port_type]}},
    }
    validated = validate_newgen_config(raw)
    assert validated["adapters"]["output_sink"]["binds"] == [port_type]


def test_validate_newgen_config_rejects_unknown_bind_port_type() -> None:
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": ["custom_port"]}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_defaults_runtime_platform_kv_backend_to_memory() -> None:
    # Missing runtime.platform.kv.backend must be normalized to memory.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    validated = validate_newgen_config(raw)
    runtime = validated["runtime"]
    assert isinstance(runtime, dict)
    platform = runtime.get("platform")
    assert isinstance(platform, dict)
    kv = platform.get("kv")
    assert isinstance(kv, dict)
    assert kv.get("backend") == "memory"


def test_validate_newgen_config_rejects_unknown_runtime_platform_kv_backend() -> None:
    # Backend value must be from the supported set.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": ["example.steps"],
            "platform": {"kv": {"backend": "redis-cluster"}},
        },
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)
