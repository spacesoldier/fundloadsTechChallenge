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
    # BOOT-CFG-04: bootstrap mode defaults to inline when omitted.
    raw = _phase0_base_config()
    validated = validate_newgen_config(raw)
    validated_runtime = validated["runtime"]
    assert isinstance(validated_runtime, dict)
    platform = validated_runtime.get("platform")
    assert isinstance(platform, dict)
    bootstrap = platform.get("bootstrap")
    assert isinstance(bootstrap, dict)
    assert bootstrap.get("mode") == "inline"


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
