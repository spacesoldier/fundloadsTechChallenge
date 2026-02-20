from __future__ import annotations

from pathlib import Path

import pytest

from stream_kernel.config.loader import load_yaml_config
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


def test_validate_newgen_config_rejects_unknown_runtime_top_level_key() -> None:
    # Runtime contract is allow-list based; unknown top-level keys must fail fast.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": ["example.steps"],
            "debug_profile": {"enabled": True},
        },
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError, match="runtime has unsupported keys"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_runtime_pipeline_as_unknown_key() -> None:
    # Legacy runtime.pipeline is removed and now rejected by strict runtime key allow-list.
    raw = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "discovery_modules": ["example.steps"],
            "pipeline": ["a", "b"],
        },
        "nodes": {},
        "adapters": {"output_sink": {"binds": []}},
    }
    with pytest.raises(ConfigError, match="runtime has unsupported keys"):
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


def _phase0_base_config() -> dict[str, object]:
    return {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"discovery_modules": ["example.steps"]},
        "nodes": {},
        "adapters": {"source": {"binds": ["stream"]}},
    }


def test_validate_newgen_config_rejects_unknown_execution_ipc_transport() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"execution_ipc": {"transport": "udp_local"}}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_execution_ipc_auth_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "token"},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_non_positive_execution_ipc_ttl() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac", "ttl_seconds": 0},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_non_positive_execution_ipc_max_payload() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac"},
            "max_payload_bytes": -1,
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_non_localhost_execution_ipc_bind_host() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "0.0.0.0",
            "auth": {"mode": "hmac"},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_valid_execution_ipc_config() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "bind_port": 0,
            "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
            "max_payload_bytes": 1024,
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    execution_ipc = platform.get("execution_ipc")
    assert isinstance(execution_ipc, dict)
    assert execution_ipc.get("transport") == "tcp_local"


def test_validate_newgen_config_defaults_runtime_platform_bootstrap_mode_to_inline() -> None:
    # BOOT-CFG-04: bootstrap mode defaults to inline when omitted and no process groups are declared.
    raw = _phase0_base_config()
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    bootstrap = platform.get("bootstrap")
    assert isinstance(bootstrap, dict)
    assert bootstrap.get("mode") == "inline"


def test_validate_newgen_config_defaults_bootstrap_mode_to_process_supervisor_when_groups_declared() -> None:
    # BOOT-CFG-11: declared process groups imply process-supervisor mode unless mode is explicitly overridden.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [{"name": "execution.cpu"}],
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "bind_port": 0,
            "auth": {"mode": "hmac", "ttl_seconds": 30, "nonce_cache_size": 1000},
            "max_payload_bytes": 1024,
        },
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    bootstrap = platform.get("bootstrap")
    assert isinstance(bootstrap, dict)
    assert bootstrap.get("mode") == "process_supervisor"


def test_validate_newgen_config_rejects_unknown_bootstrap_mode() -> None:
    # BOOT-CFG-01: unknown bootstrap mode must fail fast.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"bootstrap": {"mode": "detached_supervisor"}}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_execution_ipc_secret_mode() -> None:
    # BOOT-CFG-02: unknown execution_ipc auth secret mode must fail fast.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac", "secret_mode": "vault_agent"},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_execution_ipc_kdf() -> None:
    # BOOT-CFG-03: unsupported KDF mode must fail fast.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac", "secret_mode": "generated", "kdf": "pbkdf2"},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_requires_tcp_local_transport_for_process_supervisor_mode() -> None:
    # BOOT-CFG-05: process supervisor mode requires explicit tcp_local execution_ipc section.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"bootstrap": {"mode": "process_supervisor"}}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_process_supervisor_mode_with_valid_execution_ipc() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "bind_port": 0,
            "auth": {
                "mode": "hmac",
                "secret_mode": "generated",
                "kdf": "hkdf_sha256",
                "ttl_seconds": 30,
                "nonce_cache_size": 1000,
            },
            "max_payload_bytes": 1024,
        },
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    bootstrap = platform.get("bootstrap")
    assert isinstance(bootstrap, dict)
    assert bootstrap.get("mode") == "process_supervisor"
    execution_ipc = platform.get("execution_ipc")
    assert isinstance(execution_ipc, dict)
    auth = execution_ipc.get("auth")
    assert isinstance(auth, dict)
    assert auth.get("secret_mode") == "generated"
    assert auth.get("kdf") == "hkdf_sha256"


def test_validate_newgen_config_rejects_non_list_process_groups() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"process_groups": "web"}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_process_group_without_name() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"process_groups": [{"runner": "sync"}]}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_duplicate_process_group_names() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [{"name": "web"}, {"name": "web"}],
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_process_group_selector_field() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {"process_groups": [{"name": "web", "zones": ["a"]}]}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_web_interface_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["web"] = {"interfaces": [{"kind": "grpc"}]}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_web_interface_binds() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["web"] = {"interfaces": [{"kind": "http", "binds": ["kv"]}]}
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_valid_web_interface() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["web"] = {"interfaces": [{"kind": "http", "binds": ["request", "response"]}]}
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    web = validated_runtime.get("web")
    assert isinstance(web, dict)
    interfaces = web.get("interfaces")
    assert isinstance(interfaces, list)
    assert interfaces[0]["kind"] == "http"


def test_validate_newgen_config_keeps_memory_profile_compatible_when_new_sections_omitted() -> None:
    raw = _phase0_base_config()
    validated = validate_newgen_config(raw)
    runtime = validated["runtime"]
    assert isinstance(runtime, dict)
    platform = runtime.get("platform")
    assert isinstance(platform, dict)
    kv = platform.get("kv")
    assert isinstance(kv, dict)
    assert kv.get("backend") == "memory"


def test_validate_newgen_config_rejects_non_int_process_group_workers() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.cpu", "workers": "2"},
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_non_positive_process_group_workers() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.cpu", "workers": 0},
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_runtime_platform_readiness_timeout() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "readiness": {
            "enabled": True,
            "start_work_on_all_groups_ready": True,
            "readiness_timeout_seconds": 0,
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_observability_tracing_exporter_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "zipkin_native", "settings": {}},
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_observability_logging_exporter_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "console_colorized", "settings": {}},
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_observability_tracing_dispatch_queue_drop_policy() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "dispatch_queue": {
                "drop_policy": "drop_everything",
            }
        }
    }
    with pytest.raises(ConfigError, match="dispatch_queue\\.drop_policy must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_observability_tracing_dispatch_queue_max_items() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "dispatch_queue": {
                "max_items": 0,
            }
        }
    }
    with pytest.raises(ConfigError, match="dispatch_queue\\.max_items must be an integer > 0"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_observability_tracing_dispatch_queue_block_timeout() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "dispatch_queue": {
                "drop_policy": "block_with_timeout",
                "block_timeout_ms": 0,
            }
        }
    }
    with pytest.raises(ConfigError, match="dispatch_queue\\.block_timeout_ms must be an integer > 0"):
        validate_newgen_config(raw)


def test_validate_newgen_config_normalizes_observability_tracing_dispatch_queue_defaults() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "dispatch_queue": {
                "drop_policy": "block_with_timeout",
            }
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    dispatch_queue = tracing.get("dispatch_queue")
    assert isinstance(dispatch_queue, dict)
    assert dispatch_queue.get("drop_policy") == "block_with_timeout"
    assert dispatch_queue.get("max_items") == 131072
    assert dispatch_queue.get("block_timeout_ms") == 100
    assert dispatch_queue.get("forward_batch_max_items") == 100
    assert dispatch_queue.get("forward_flush_interval_ms") == 20
    assert dispatch_queue.get("drain_timeout_seconds") == 30.0


def test_validate_newgen_config_rejects_unknown_observability_monitoring_exporter_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "monitoring": {
            "exporters": [
                {"kind": "graphite", "settings": {}},
            ]
        }
    }
    with pytest.raises(ConfigError, match="monitoring\\.exporters\\[0\\]\\.kind must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_observability_monitoring_prometheus_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "monitoring": {
            "exporters": [
                {"kind": "prometheus", "settings": {"mode": "pushgateway"}},
            ]
        }
    }
    with pytest.raises(ConfigError, match="monitoring\\.exporters\\[0\\]\\.settings\\.mode must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_observability_monitoring_prometheus_http_pull_defaults() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "monitoring": {
            "exporters": [
                {"kind": "prometheus", "settings": {"mode": "http_pull"}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    monitoring = observability.get("monitoring")
    assert isinstance(monitoring, dict)
    exporters = monitoring.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    http = settings.get("http")
    assert isinstance(http, dict)
    assert http.get("host") == "127.0.0.1"
    assert http.get("port") == 9464
    assert http.get("path") == "/metrics"


def test_validate_newgen_config_accepts_observability_service_worker_defaults() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "service_worker": {
            "enabled": True,
        }
    }

    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    service_worker = observability.get("service_worker")
    assert isinstance(service_worker, dict)
    assert service_worker.get("enabled") is True
    assert service_worker.get("queue_max_items") == 131072
    assert service_worker.get("drop_policy") == "drop_newest"
    assert service_worker.get("block_timeout_ms") == 100
    assert service_worker.get("drain_timeout_seconds") == 30.0


def test_validate_newgen_config_rejects_observability_service_worker_invalid_drop_policy() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "service_worker": {
            "enabled": True,
            "drop_policy": "block_forever",
        }
    }

    with pytest.raises(ConfigError, match="service_worker\\.drop_policy must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_otel_exporter_queue_block_timeout() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "urllib",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "queue": {
                            "drop_policy": "block_with_timeout",
                            "block_timeout_ms": 0,
                        },
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="queue\\.block_timeout_ms must be an integer > 0"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_observability_logging_lifecycle_level() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "lifecycle_events": {"enabled": True, "level": "warning"},
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_observability_logging_lifecycle_level_off_none_full() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "lifecycle_events": {"enabled": True, "level": "off"},
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    lifecycle = logging.get("lifecycle_events")
    assert isinstance(lifecycle, dict)
    assert lifecycle.get("level") == "off"

    runtime["observability"] = {
        "logging": {
            "lifecycle_events": {"enabled": True, "level": "none"},
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    lifecycle = logging.get("lifecycle_events")
    assert isinstance(lifecycle, dict)
    assert lifecycle.get("level") == "none"

    runtime["observability"] = {
        "logging": {
            "lifecycle_events": {"enabled": True, "level": "full"},
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    lifecycle = logging.get("lifecycle_events")
    assert isinstance(lifecycle, dict)
    assert lifecycle.get("level") == "full"


def test_validate_newgen_config_accepts_observability_logging_stdout_plain_exporter_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "stdout_plain", "settings": {}},
            ],
            "lifecycle_events": {"enabled": True, "level": "info"},
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    exporters = logging.get("exporters")
    assert isinstance(exporters, list)
    assert exporters and exporters[0].get("kind") == "stdout_plain"


def test_validate_newgen_config_accepts_observability_logging_exporter_mode_all() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "jsonl", "mode": "all", "settings": {"path": "logs/all.jsonl"}},
            ],
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    exporters = logging.get("exporters")
    assert isinstance(exporters, list)
    assert exporters and exporters[0].get("mode") == "all"


def test_validate_newgen_config_rejects_observability_logging_exporter_unknown_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "jsonl", "mode": "everything", "settings": {"path": "logs/all.jsonl"}},
            ],
        }
    }
    with pytest.raises(ConfigError, match="exporters\\[0\\]\\.mode must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_logging_jsonl_exporter_without_path() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "jsonl", "settings": {}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    exporters = logging.get("exporters")
    assert isinstance(exporters, list)
    assert exporters and exporters[0].get("kind") == "jsonl"


def test_validate_newgen_config_accepts_logging_file_plain_exporter_without_path() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "file_plain", "settings": {}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    exporters = logging.get("exporters")
    assert isinstance(exporters, list)
    assert exporters and exporters[0].get("kind") == "file_plain"


def test_validate_newgen_config_rejects_logging_jsonl_exporter_invalid_workers_dir() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "logging": {
            "exporters": [
                {"kind": "jsonl", "settings": {"path": "logs/lifecycle.jsonl", "workers_dir": ""}},
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


@pytest.mark.parametrize("slice_name", ["all", "business_logic", "platform_internals"])
def test_validate_newgen_config_accepts_tracing_jsonl_trace_slice(slice_name: str) -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "jsonl", "settings": {"path": "logs/trace.jsonl", "trace_slice": slice_name}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    assert settings.get("trace_slice") == slice_name


@pytest.mark.parametrize(
    ("raw_value", "normalized"),
    [("logical", "business_logic"), ("topology", "platform_internals"), ("full", "all")],
)
def test_validate_newgen_config_normalizes_tracing_jsonl_trace_slice_aliases(
    raw_value: str,
    normalized: str,
) -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "jsonl", "settings": {"path": "logs/trace.jsonl", "trace_slice": raw_value}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    assert settings.get("trace_slice") == normalized


def test_validate_newgen_config_normalizes_tracing_jsonl_view_trace_view_alias() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": "logs/trace.jsonl", "view": {"trace_view": "topology"}},
                },
            ]
        }
    }
    validated = validate_newgen_config(raw)
    vruntime = validated.get("runtime")
    assert isinstance(vruntime, dict)
    observability = vruntime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    assert settings.get("trace_slice") == "platform_internals"


def test_validate_newgen_config_rejects_tracing_jsonl_invalid_trace_slice() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "jsonl", "settings": {"path": "logs/trace.jsonl", "trace_slice": "unknown"}},
            ]
        }
    }
    with pytest.raises(ConfigError, match="trace_slice must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_tracing_jsonl_non_mapping_view() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "jsonl", "settings": {"path": "logs/trace.jsonl", "view": "logical"}},
            ]
        }
    }
    with pytest.raises(ConfigError, match="settings.view must be a mapping"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_tracing_jsonl_invalid_view_trace_view() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "jsonl",
                    "settings": {"path": "logs/trace.jsonl", "view": {"trace_view": "both"}},
                },
            ]
        }
    }
    with pytest.raises(ConfigError, match="settings.view.trace_view must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_k_a_01_rejects_mixed_pipeline_and_legacy_exporters() -> None:
    # OBS-K-A-01: unified observability pipeline cannot be mixed with legacy exporter blocks.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "tracing_only",
        },
        "tracing": {
            "exporters": [
                {"kind": "stdout", "settings": {}},
            ]
        },
    }
    with pytest.raises(ConfigError, match="pipeline cannot be combined"):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_mixed_pipeline_and_monitoring_exporters() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "tracing_only",
        },
        "monitoring": {
            "exporters": [
                {"kind": "prometheus", "settings": {"mode": "textfile"}},
            ],
        },
    }
    with pytest.raises(ConfigError, match="pipeline cannot be combined"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_k_a_02_rejects_unknown_pipeline_system_node_kind() -> None:
    # OBS-K-A-02: system node kinds must be from the frozen allow-list.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "tracing_only",
            "system_nodes": [
                {"kind": "system.obs.custom_dispatch"},
            ],
        }
    }
    with pytest.raises(ConfigError, match="system_nodes\\[0\\]\\.kind must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_k_a_03_accepts_tracing_only_pipeline_defaults() -> None:
    # OBS-K-A-03: tracing-only profile is valid with default stream set.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "tracing_only",
            "system_nodes": [
                {"kind": "system.obs.trace_dispatch"},
            ],
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    pipeline = observability.get("pipeline")
    assert isinstance(pipeline, dict)
    assert pipeline.get("mode") == "tracing_only"
    assert pipeline.get("streams") == ["tracing"]


def test_validate_newgen_config_obs_k_a_04_accepts_full_multi_stream_pipeline() -> None:
    # OBS-K-A-04: full multi-stream profile is valid and keeps declared streams.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "full_multi_stream",
            "streams": ["tracing", "logging", "telemetry", "monitoring"],
            "system_nodes": [
                {"kind": "system.obs.trace_dispatch"},
                {"kind": "system.obs.log_dispatch"},
                {"kind": "system.obs.metric_dispatch"},
                {"kind": "system.obs.monitor_dispatch"},
            ],
            "strict_bindings": True,
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    pipeline = observability.get("pipeline")
    assert isinstance(pipeline, dict)
    assert pipeline.get("mode") == "full_multi_stream"
    streams = pipeline.get("streams")
    assert isinstance(streams, list)
    assert set(streams) == {"tracing", "logging", "telemetry", "monitoring"}


def test_validate_newgen_config_obs_k_a_05_rejects_invalid_full_multi_stream_shape() -> None:
    # OBS-K-A-05: full_multi_stream must include all frozen stream kinds.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "pipeline": {
            "mode": "full_multi_stream",
            "streams": ["tracing", "logging"],
        }
    }
    with pytest.raises(ConfigError, match="mode=full_multi_stream requires streams"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_01_rejects_unknown_otel_backend() -> None:
    # OBS-CFG-A-01
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "curl",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_02_rejects_async_backend_for_sync_runner_without_bridge() -> None:
    # OBS-CFG-A-02
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.cpu", "runner_profile": "sync"},
        ]
    }
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "aiohttp",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_03_rejects_invalid_batching_and_retry_bounds() -> None:
    # OBS-CFG-A-03
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "urllib",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "batch": {"max_items": 0},
                        "retry": {"max_attempts": -1},
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_04_backward_compat_defaults_backend_to_urllib() -> None:
    # OBS-CFG-A-04
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    assert exporters[0].get("backend") == "urllib"


def test_validate_newgen_config_obs_cfg_a_04b_allows_zero_batch_flush_interval() -> None:
    # OBS-CFG-A-04B: flush_interval_ms=0 disables timer flush and must be accepted.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "batch": {"max_items": 1024, "flush_interval_ms": 0},
                    },
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    batch = settings.get("batch")
    assert isinstance(batch, dict)
    assert batch.get("flush_interval_ms") == 0


def test_validate_newgen_config_obs_cfg_a_04d_applies_near_realtime_otlp_defaults() -> None:
    # OBS-CFG-E-01: OTLP exporter defaults should favor near-realtime small-batch flush.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    batch = settings.get("batch")
    assert isinstance(batch, dict)
    assert batch.get("max_items") == 64
    assert batch.get("flush_interval_ms") == 200
    queue = settings.get("queue")
    assert isinstance(queue, dict)
    assert queue.get("max_items") == 10000
    assert queue.get("drop_policy") == "block_with_timeout"
    assert queue.get("block_timeout_ms") == 100


def test_validate_newgen_config_obs_cfg_a_04c_accepts_transport_group_backend() -> None:
    # OBS-CFG-A-04C: backend may be declared in settings.transport.backend and must normalize to exporter.backend.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "otlp": {"endpoint": "http://collector:4318/v1/traces"},
                        "transport": {"backend": "httpx", "httpx": {"mode": "async"}},
                    },
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    assert exporters[0].get("backend") == "httpx"
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    assert settings.get("endpoint") == "http://collector:4318/v1/traces"
    assert settings.get("httpx", {}).get("mode") == "async"


def test_validate_newgen_config_obs_cfg_a_04d_accepts_settings_backend_for_compat() -> None:
    # OBS-CFG-A-04D: settings.backend remains accepted and normalized to exporter.backend for compatibility.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "backend": "aiohttp",
                    },
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    assert exporters[0].get("backend") == "aiohttp"


def test_validate_newgen_config_obs_cfg_a_04a_accepts_otel_dual_view_kinds() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {"kind": "otel_otlp_logical", "settings": {"endpoint": "http://collector:4318/v1/traces"}},
                {"kind": "otel_otlp_topology", "settings": {"endpoint": "http://collector:4318/v1/traces"}},
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    assert exporters[0].get("kind") == "otel_otlp_logical"
    assert exporters[1].get("kind") == "otel_otlp_topology"
    assert exporters[0].get("backend") == "urllib"
    assert exporters[1].get("backend") == "urllib"


def test_validate_newgen_config_obs_cfg_a_04b_rejects_invalid_exporter_enabled_type() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "enabled": "yes",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="enabled must be a boolean"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_04c_rejects_invalid_otel_trace_view() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "trace_view": "both",
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="trace_view must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_04d_rejects_invalid_service_name_by_step_type() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "service_name_by_step": "yes",
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="service_name_by_step must be a boolean"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_04e_rejects_invalid_isolate_view_ids_type() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "isolate_view_ids": 1,
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="isolate_view_ids must be a boolean"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_e_01_rejects_invalid_dependency_missing_mode() -> None:
    # OBS-K-E-03: dependency_missing mode must be explicit and constrained.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "requests",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "dependency_missing": "fallback_to_stdout",
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError, match="dependency_missing must be one of"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_e_02_accepts_degrade_noop_dependency_mode() -> None:
    # OBS-K-E-04: explicit degrade mode should be normalized and preserved.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "requests",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "dependency_missing": "degrade_noop",
                    },
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    exporter0 = exporters[0]
    assert isinstance(exporter0, dict)
    settings = exporter0.get("settings")
    assert isinstance(settings, dict)
    assert settings.get("dependency_missing") == "degrade_noop"


def test_validate_newgen_config_obs_cfg_a_05_rejects_sync_group_with_async_only_dependency_without_bridge() -> None:
    # OBS-CFG-A-05
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.sync", "runner_profile": "sync"},
            {"name": "execution.async", "runner_profile": "async"},
        ]
    }
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "aiohttp",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_cfg_a_05b_allows_async_backend_without_explicit_runner_profile() -> None:
    # RUN-AUTO-CFG-02: backend/profile compatibility checks apply only to explicit overrides.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.group"},
        ]
    }
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "aiohttp",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    exporter0 = exporters[0]
    assert isinstance(exporter0, dict)
    assert exporter0.get("backend") == "aiohttp"


def test_validate_newgen_config_obs_cfg_a_06_rejects_async_group_with_sync_dependency_without_bridge() -> None:
    # OBS-CFG-A-06
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.async", "runner_profile": "async"},
        ]
    }
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "urllib3",
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


@pytest.mark.parametrize(
    ("runner_profile", "backend", "should_pass"),
    [
        ("sync", "urllib", True),
        ("sync", "requests", True),
        ("sync", "httpx", True),
        ("sync", "aiohttp", False),
        ("sync", "urllib3", True),
        ("sync", "grpcio", True),
        ("sync", "otel_sdk", True),
        ("async", "urllib", False),
        ("async", "requests", False),
        ("async", "httpx", True),
        ("async", "aiohttp", True),
        ("async", "urllib3", False),
        ("async", "grpcio", False),
        ("async", "otel_sdk", False),
    ],
)
def test_validate_newgen_config_obs_mat_01_backend_runner_profile_matrix(
    runner_profile: str,
    backend: str,
    should_pass: bool,
) -> None:
    # OBS-MAT-01: full backend/profile matrix must either pass or fail with deterministic validation reason.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {"name": "execution.group", "runner_profile": runner_profile},
        ]
    }
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": backend,
                    "settings": {"endpoint": "http://collector:4318/v1/traces"},
                }
            ]
        }
    }

    if should_pass:
        validated = validate_newgen_config(raw)
        validated_runtime = validated["runtime"]
        assert isinstance(validated_runtime, dict)
        observability = validated_runtime.get("observability")
        assert isinstance(observability, dict)
        tracing = observability.get("tracing")
        assert isinstance(tracing, dict)
        exporters = tracing.get("exporters")
        assert isinstance(exporters, list)
        assert exporters[0].get("backend") == backend
        return

    with pytest.raises(ConfigError, match="requires settings\\.bridge=true"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_httpx_cfg_01_accepts_httpx_settings() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "httpx",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "httpx": {
                            "mode": "async",
                            "http2": True,
                            "max_connections": 64,
                            "max_keepalive_connections": 16,
                        },
                    },
                }
            ]
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    httpx = settings.get("httpx")
    assert isinstance(httpx, dict)
    assert httpx.get("mode") == "async"
    assert httpx.get("http2") is True
    assert httpx.get("max_connections") == 64
    assert httpx.get("max_keepalive_connections") == 16


def test_validate_newgen_config_obs_httpx_cfg_02_rejects_invalid_httpx_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "httpx",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "httpx": {"mode": "cooperative"},
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_aio_cfg_01_accepts_aiohttp_settings() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "aiohttp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "aiohttp": {
                            "shutdown_timeout_seconds": 3.5,
                            "connector_limit": 128,
                            "connector_limit_per_host": 32,
                        },
                    },
                }
            ]
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    aiohttp = settings.get("aiohttp")
    assert isinstance(aiohttp, dict)
    assert aiohttp.get("shutdown_timeout_seconds") == 3.5
    assert aiohttp.get("connector_limit") == 128
    assert aiohttp.get("connector_limit_per_host") == 32


def test_validate_newgen_config_obs_aio_cfg_02_rejects_invalid_shutdown_timeout() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "aiohttp",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "aiohttp": {"shutdown_timeout_seconds": 0},
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_u3_cfg_01_accepts_urllib3_settings() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "urllib3",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "urllib3": {
                            "num_pools": 8,
                            "maxsize": 32,
                            "block": True,
                            "timeout_seconds": 1.75,
                        },
                    },
                }
            ]
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    urllib3 = settings.get("urllib3")
    assert isinstance(urllib3, dict)
    assert urllib3.get("num_pools") == 8
    assert urllib3.get("maxsize") == 32
    assert urllib3.get("block") is True
    assert urllib3.get("timeout_seconds") == 1.75


def test_validate_newgen_config_obs_u3_cfg_02_rejects_invalid_urllib3_timeout() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "urllib3",
                    "settings": {
                        "endpoint": "http://collector:4318/v1/traces",
                        "urllib3": {"timeout_seconds": 0},
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_grpc_cfg_01_accepts_grpc_settings() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "grpcio",
                    "settings": {
                        "endpoint": "collector:4317",
                        "grpc": {
                            "insecure": False,
                            "timeout_seconds": 1.5,
                            "retryable_status_codes": ["UNAVAILABLE", "DEADLINE_EXCEEDED"],
                        },
                    },
                }
            ]
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    settings = exporters[0].get("settings")
    assert isinstance(settings, dict)
    grpc = settings.get("grpc")
    assert isinstance(grpc, dict)
    assert grpc.get("insecure") is False
    assert grpc.get("timeout_seconds") == 1.5
    retryable = grpc.get("retryable_status_codes")
    assert isinstance(retryable, list)
    assert retryable == ["UNAVAILABLE", "DEADLINE_EXCEEDED"]


def test_validate_newgen_config_obs_grpc_cfg_02_rejects_invalid_retryable_status_codes() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {
            "exporters": [
                {
                    "kind": "otel_otlp",
                    "backend": "grpcio",
                    "settings": {
                        "endpoint": "collector:4317",
                        "grpc": {"retryable_status_codes": [123]},
                    },
                }
            ]
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_execution_ipc_control_bind_host() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac"},
            "control": {
                "transport": "tcp_local",
                "bind_host": "0.0.0.0",
                "auth": {"mode": "hmac"},
            },
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_phase5pre_stepa_contract_and_defaults() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["observability"] = {
        "tracing": {"exporters": [{"kind": "otel_otlp", "settings": {"endpoint": "http://collector:4318"}}]},
        "logging": {
            "exporters": [{"kind": "stdout"}],
            "lifecycle_events": {"enabled": True, "level": "debug"},
        },
    }
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {
            "transport": "tcp_local",
            "bind_host": "127.0.0.1",
            "auth": {"mode": "hmac"},
            "control": {
                "transport": "tcp_local",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "auth": {"mode": "hmac", "ttl_seconds": 15, "nonce_cache_size": 2048},
                "max_payload_bytes": 8192,
            },
        },
        "readiness": {
            "enabled": True,
            "start_work_on_all_groups_ready": True,
            "readiness_timeout_seconds": 45,
        },
        "process_groups": [
            {
                "name": "execution.ingress",
                "nodes": ["source:source", "ingress_line_bridge"],
                "workers": 2,
                "runner_profile": "sync",
                "heartbeat_seconds": 3,
                "start_timeout_seconds": 20,
                "stop_timeout_seconds": 25,
            }
        ],
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    logging = observability.get("logging")
    assert isinstance(logging, dict)
    lifecycle_events = logging.get("lifecycle_events")
    assert isinstance(lifecycle_events, dict)
    assert lifecycle_events.get("level") == "debug"
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    readiness = platform.get("readiness")
    assert isinstance(readiness, dict)
    assert readiness.get("readiness_timeout_seconds") == 45
    process_groups = platform.get("process_groups")
    assert isinstance(process_groups, list)
    assert process_groups[0]["workers"] == 2


def test_validate_newgen_config_accepts_runtime_platform_routing_cache_contract() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "routing_cache": {
            "enabled": True,
            "negative_cache": True,
            "max_entries": 1234,
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    routing_cache = platform.get("routing_cache")
    assert isinstance(routing_cache, dict)
    assert routing_cache.get("enabled") is True
    assert routing_cache.get("negative_cache") is True
    assert routing_cache.get("max_entries") == 1234


def test_validate_newgen_config_rejects_runtime_platform_routing_cache_unknown_keys() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "routing_cache": {
            "enabled": True,
            "unknown": "x",
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_runtime_platform_routing_cache_bad_max_entries() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "routing_cache": {
            "enabled": True,
            "negative_cache": True,
            "max_entries": 0,
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_runtime_platform_boundary_dispatch_contract() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "batch",
            "batch_max_items": 16,
            "control_poll_ms": 2.5,
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    boundary_dispatch = platform.get("boundary_dispatch")
    assert isinstance(boundary_dispatch, dict)
    assert boundary_dispatch.get("mode") == "batch"
    assert boundary_dispatch.get("batch_max_items") == 16
    assert boundary_dispatch.get("stream_batch_max_items") == 1
    assert boundary_dispatch.get("control_poll_ms") == 2.5
    assert boundary_dispatch.get("timeout_seconds") == 10.0


def test_validate_newgen_config_rejects_runtime_platform_boundary_dispatch_unknown_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "burst",
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_runtime_platform_boundary_dispatch_bad_control_poll_ms() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "stream",
            "control_poll_ms": 0,
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_runtime_platform_boundary_dispatch_timeout_seconds() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "stream",
            "timeout_seconds": 45.0,
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    boundary_dispatch = platform.get("boundary_dispatch")
    assert isinstance(boundary_dispatch, dict)
    assert boundary_dispatch.get("timeout_seconds") == 45.0


def test_validate_newgen_config_accepts_runtime_platform_boundary_dispatch_stream_batch_max_items() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "stream",
            "stream_batch_max_items": 8,
        }
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    boundary_dispatch = platform.get("boundary_dispatch")
    assert isinstance(boundary_dispatch, dict)
    assert boundary_dispatch.get("stream_batch_max_items") == 8


def test_validate_newgen_config_rejects_runtime_platform_boundary_dispatch_bad_stream_batch_max_items() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "boundary_dispatch": {
            "mode": "stream",
            "stream_batch_max_items": 0,
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_multiprocess_jaeger_config_uses_async_otel_backend_experiment() -> None:
    root = Path(__file__).resolve().parents[3]
    raw = load_yaml_config(root / "src" / "fund_load" / "experiment_config_newgen_multiprocess_jaeger.yml")
    validated = validate_newgen_config(raw)
    runtime = validated["runtime"]
    assert isinstance(runtime, dict)
    observability = runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    enabled_otel = [
        exporter
        for exporter in exporters
        if isinstance(exporter, dict)
        and exporter.get("enabled", True) is True
        and isinstance(exporter.get("kind"), str)
        and str(exporter.get("kind")).startswith("otel_otlp")
    ]
    assert enabled_otel
    for exporter in enabled_otel:
        settings = exporter.get("settings")
        assert isinstance(settings, dict)
        assert settings.get("backend") == "httpx"
        httpx_settings = settings.get("httpx")
        assert isinstance(httpx_settings, dict)
        assert httpx_settings.get("mode") == "async"


def test_validate_multiprocess_jaeger_config_uses_async_otel_backend_baseline() -> None:
    root = Path(__file__).resolve().parents[3]
    raw = load_yaml_config(root / "src" / "fund_load" / "baseline_config_newgen_multiprocess_jaeger.yml")
    validated = validate_newgen_config(raw)
    runtime = validated["runtime"]
    assert isinstance(runtime, dict)
    observability = runtime.get("observability")
    assert isinstance(observability, dict)
    tracing = observability.get("tracing")
    assert isinstance(tracing, dict)
    exporters = tracing.get("exporters")
    assert isinstance(exporters, list)
    otel = [
        exporter
        for exporter in exporters
        if isinstance(exporter, dict)
        and isinstance(exporter.get("kind"), str)
        and str(exporter.get("kind")).startswith("otel_otlp")
    ]
    assert otel
    for exporter in otel:
        settings = exporter.get("settings")
        assert isinstance(settings, dict)
        assert settings.get("backend") == "httpx"
        httpx_settings = settings.get("httpx")
        assert isinstance(httpx_settings, dict)
        assert httpx_settings.get("mode") == "async"


def test_validate_newgen_config_accepts_runtime_platform_api_policies_and_web_interface_policies() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "api_policies": {
            "defaults": {
                "timeout_ms": 2000,
                "retry": {"max_attempts": 3, "backoff_ms": 100},
                "rate_limit": {
                    "kind": "token_bucket",
                    "refill_rate_per_sec": 25,
                    "bucket_capacity": 100,
                },
            },
            "profiles": {
                "partner_api": {
                    "timeout_ms": 1500,
                    "rate_limit": {
                        "kind": "fixed_window",
                        "limit": 50,
                        "window_ms": 1000,
                    },
                }
            },
        }
    }
    runtime["web"] = {
        "interfaces": [
            {
                "kind": "http",
                "binds": ["request", "response"],
                "policies": {
                    "request_size_bytes": 1048576,
                    "timeout_ms": 5000,
                    "rate_limit": {
                        "kind": "sliding_window_counter",
                        "limit": 200,
                        "window_ms": 60000,
                    },
                },
            }
        ]
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    api_policies = platform.get("api_policies")
    assert isinstance(api_policies, dict)
    defaults = api_policies.get("defaults")
    assert isinstance(defaults, dict)
    assert defaults.get("timeout_ms") == 2000
    assert defaults.get("rate_limit", {}).get("kind") == "token_bucket"

    web = validated_runtime.get("web")
    assert isinstance(web, dict)
    interfaces = web.get("interfaces")
    assert isinstance(interfaces, list)
    policies = interfaces[0].get("policies")
    assert isinstance(policies, dict)
    assert policies.get("request_size_bytes") == 1048576
    assert policies.get("rate_limit", {}).get("kind") == "sliding_window_counter"


def test_validate_newgen_config_rejects_unknown_runtime_platform_api_rate_limiter_kind() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "api_policies": {
            "defaults": {
                "rate_limit": {
                    "kind": "fixed_quota",
                    "limit": 10,
                    "window_ms": 1000,
                }
            }
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_token_bucket_without_capacity() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "api_policies": {
            "defaults": {
                "rate_limit": {
                    "kind": "token_bucket",
                    "refill_rate_per_sec": 10,
                }
            }
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_unknown_web_interface_policy_key() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["web"] = {
        "interfaces": [
            {
                "kind": "http",
                "binds": ["request", "response"],
                "policies": {"burst_mode": True},
            }
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_rejects_invalid_web_interface_request_size_policy() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["web"] = {
        "interfaces": [
            {
                "kind": "http",
                "binds": ["request", "response"],
                "policies": {"request_size_bytes": 0},
            }
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_accepts_process_group_services_contract() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {
                "name": "execution.cpu",
                "runner_profile": "sync",
                "services": {
                    "api_service_profile": "partner_api",
                    "rate_limiter_profile": "partner_api",
                },
            }
        ],
        "api_policies": {
            "profiles": {
                "partner_api": {
                    "execution_mode": "sync",
                    "rate_limit": {"kind": "fixed_window", "limit": 10, "window_ms": 1000},
                }
            }
        },
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    process_groups = platform.get("process_groups")
    assert isinstance(process_groups, list)
    services = process_groups[0].get("services")
    assert isinstance(services, dict)
    assert services.get("api_service_profile") == "partner_api"


def test_validate_newgen_config_rejects_unknown_process_group_runner_profile() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {
                "name": "execution.cpu",
                "runner_profile": "celery",
            }
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_allows_process_group_without_runner_profile() -> None:
    # RUN-AUTO-CFG-01: runner_profile is optional; runtime can infer runner automatically.
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {
                "name": "execution.cpu",
                "workers": 2,
            }
        ]
    }
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    group = groups[0]
    assert isinstance(group, dict)
    assert "runner_profile" not in group


def test_validate_newgen_config_rejects_unknown_process_group_services_key() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "process_groups": [
            {
                "name": "execution.cpu",
                "services": {"policy_profile": "p1"},
            }
        ]
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_service_process_materializes_default_owner_group() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [{"name": "execution.cpu", "nodes": ["compute_features"]}],
    }
    runtime["observability"] = {
        "service_process": {"enabled": True},
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    system_groups = [group for group in groups if isinstance(group, dict) and group.get("name") == "system.observability"]
    assert len(system_groups) == 1
    system_group = system_groups[0]
    assert system_group.get("runner_profile") == "async"
    assert system_group.get("workers") == 1
    nodes = system_group.get("nodes")
    assert isinstance(nodes, list)
    assert "system.obs.trace_dispatch" in nodes
    assert "system.obs.log_dispatch" in nodes
    assert "system.obs.metric_dispatch" in nodes
    assert "system.obs.monitor_dispatch" in nodes


def test_validate_newgen_config_worker_queue_telemetry_adds_system_dispatch_node_to_owner_group() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [{"name": "execution.cpu", "nodes": ["compute_features"]}],
    }
    runtime["observability"] = {
        "service_process": {"enabled": True},
        "worker_queue_telemetry": {"enabled": True, "sample_hz": 20},
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    service_process = observability.get("service_process")
    assert isinstance(service_process, dict)
    nodes = service_process.get("nodes")
    assert isinstance(nodes, list)
    assert "system.obs.worker_queue_dispatch" in nodes

    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    owner = next(
        group
        for group in groups
        if isinstance(group, dict) and group.get("name") == "system.observability"
    )
    owner_nodes = owner.get("nodes")
    assert isinstance(owner_nodes, list)
    assert "system.obs.worker_queue_dispatch" in owner_nodes


def test_validate_newgen_config_obs_service_process_defaults_to_enabled_for_process_supervisor_with_exporters() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [{"name": "execution.cpu", "nodes": ["compute_features"]}],
    }
    runtime["observability"] = {
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    service_process = observability.get("service_process")
    assert isinstance(service_process, dict)
    assert service_process.get("enabled") is True
    assert service_process.get("group_name") == "system.observability"
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    assert any(
        isinstance(group, dict) and group.get("name") == "system.observability"
        for group in groups
    )


def test_validate_newgen_config_obs_service_process_can_be_explicitly_disabled() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [{"name": "execution.cpu", "nodes": ["compute_features"]}],
    }
    runtime["observability"] = {
        "service_process": {"enabled": False},
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    observability = validated_runtime.get("observability")
    assert isinstance(observability, dict)
    service_process = observability.get("service_process")
    assert isinstance(service_process, dict)
    assert service_process.get("enabled") is False
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    assert not any(
        isinstance(group, dict) and group.get("name") == "system.observability"
        for group in groups
    )


def test_validate_newgen_config_obs_service_process_rejects_ambiguous_system_node_ownership() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [
            {"name": "execution.cpu", "nodes": ["compute_features", "system.obs.trace_dispatch"]},
            {"name": "system.observability", "nodes": ["system.obs.trace_dispatch"]},
        ],
    }
    runtime["observability"] = {
        "service_process": {"enabled": True},
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    with pytest.raises(ConfigError, match="owned by multiple groups"):
        validate_newgen_config(raw)


def test_validate_newgen_config_obs_service_process_rejects_missing_owner_when_auto_create_disabled() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "bootstrap": {"mode": "process_supervisor"},
        "execution_ipc": {"transport": "tcp_local", "auth": {"mode": "hmac"}},
        "process_groups": [{"name": "execution.cpu", "nodes": ["compute_features"]}],
    }
    runtime["observability"] = {
        "service_process": {"enabled": True, "auto_create_group": False},
        "tracing": {"exporters": [{"kind": "jsonl", "settings": {"path": "traces/trace.jsonl"}}]},
    }

    with pytest.raises(ConfigError, match="owner group is not resolvable"):
        validate_newgen_config(raw)


@pytest.mark.parametrize(
    "config_path,expect_enabled",
    [
        ("src/fund_load/baseline_config_newgen_multiprocess.yml", False),
        ("src/fund_load/experiment_config_newgen_multiprocess.yml", False),
        ("src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml", True),
        ("src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml", True),
    ],
)
def test_validate_newgen_config_fund_load_multiprocess_regression_service_process_defaults(
    config_path: str,
    expect_enabled: bool,
) -> None:
    validated = validate_newgen_config(load_yaml_config(Path(config_path)))
    runtime = validated.get("runtime")
    assert isinstance(runtime, dict)
    observability = runtime.get("observability")
    if not isinstance(observability, dict):
        assert expect_enabled is False
        return
    service_process = observability.get("service_process")
    assert isinstance(service_process, dict)
    assert service_process.get("enabled") is expect_enabled
    platform = runtime.get("platform")
    assert isinstance(platform, dict)
    groups = platform.get("process_groups")
    assert isinstance(groups, list)
    has_observability_group = any(
        isinstance(group, dict) and group.get("name") == "system.observability"
        for group in groups
    )
    assert has_observability_group is expect_enabled


def test_validate_newgen_config_rejects_unknown_api_policy_execution_mode() -> None:
    raw = _phase0_base_config()
    runtime = raw["runtime"]
    assert isinstance(runtime, dict)
    runtime["platform"] = {
        "api_policies": {
            "profiles": {
                "partner_api": {
                    "execution_mode": "io",
                    "rate_limit": {"kind": "fixed_window", "limit": 10, "window_ms": 1000},
                }
            }
        }
    }
    with pytest.raises(ConfigError):
        validate_newgen_config(raw)
