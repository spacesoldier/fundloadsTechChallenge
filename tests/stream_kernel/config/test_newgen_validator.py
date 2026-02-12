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


def test_validate_newgen_config_requires_adapter_entry_mapping() -> None:
    raw = {"version": 1, "scenario": {"name": "baseline"}, "nodes": {}, "adapters": {"some_adapter": "x"}}
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


def test_validate_newgen_config_defaults_runtime_ordering_sink_mode_to_completion() -> None:
    # Ordering mode defaults to completion-order when omitted.
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
    ordering = runtime.get("ordering")
    assert isinstance(ordering, dict)
    assert ordering.get("sink_mode") == "completion"


@pytest.mark.parametrize("mode", ["completion", "source_seq"])
def test_validate_newgen_config_accepts_runtime_ordering_sink_mode(mode: str) -> None:
    # Runtime ordering mode is validated against framework-supported values.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": ["example.steps"],
            "ordering": {"sink_mode": mode},
        },
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    validated = validate_newgen_config(raw)
    runtime = validated["runtime"]
    assert isinstance(runtime, dict)
    ordering = runtime.get("ordering")
    assert isinstance(ordering, dict)
    assert ordering.get("sink_mode") == mode


def test_validate_newgen_config_rejects_unknown_runtime_ordering_sink_mode() -> None:
    # Unknown ordering mode must fail fast.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": ["example.steps"],
            "ordering": {"sink_mode": "stable_sort"},
        },
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


@pytest.mark.parametrize(
    "fmt",
    ["text/jsonl", "text/plain", "application/octet-stream"],
)
def test_validate_newgen_config_accepts_supported_adapter_format_values(fmt: str) -> None:
    # Adapter format hint is validated against the framework-supported transport set.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "ingress_file": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": fmt},
            }
        },
    }
    validated = validate_newgen_config(raw)
    adapters = validated["adapters"]
    assert isinstance(adapters, dict)
    ingress = adapters["ingress_file"]
    assert isinstance(ingress, dict)
    settings = ingress["settings"]
    assert isinstance(settings, dict)
    assert settings["format"] == fmt


@pytest.mark.parametrize("mode", ["strict", "replace"])
def test_validate_newgen_config_accepts_adapter_decode_errors_policy(mode: str) -> None:
    # Decode policy is adapter-level transport setting for text formats.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "ingress_file": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": "text/plain", "decode_errors": mode},
            }
        },
    }
    validated = validate_newgen_config(raw)
    adapters = validated["adapters"]
    assert isinstance(adapters, dict)
    ingress = adapters["ingress_file"]
    assert isinstance(ingress, dict)
    settings = ingress["settings"]
    assert isinstance(settings, dict)
    assert settings["decode_errors"] == mode


def test_validate_newgen_config_rejects_unknown_adapter_decode_errors_policy() -> None:
    # Unknown decode policy must fail fast.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "ingress_file": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": "text/plain", "decode_errors": "skip"},
            }
        },
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_adapter_encoding_setting() -> None:
    # Text encoding is adapter-level transport setting and should be preserved.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "source": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": "text/plain", "encoding": "utf-16-le"},
            }
        },
    }
    validated = validate_newgen_config(raw)
    adapters = validated["adapters"]
    assert isinstance(adapters, dict)
    source = adapters["source"]
    assert isinstance(source, dict)
    settings = source["settings"]
    assert isinstance(settings, dict)
    assert settings["encoding"] == "utf-16-le"


def test_validate_newgen_config_rejects_non_string_adapter_encoding() -> None:
    # Invalid encoding type must fail fast.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "source": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": "text/plain", "encoding": 123},
            }
        },
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_adapter_format_value() -> None:
    # Unknown format must fail fast during validation.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {
            "ingress_file": {
                "binds": ["stream"],
                "settings": {"path": "input.txt", "format": "text/csv"},
            }
        },
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)
