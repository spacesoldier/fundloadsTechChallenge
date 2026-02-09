from __future__ import annotations


class ConfigError(ValueError):
    # Raised for invalid framework config (fail fast).
    pass


_STABLE_PORT_TYPES = {"stream", "kv_stream", "kv", "request", "response", "service"}
_SUPPORTED_KV_BACKENDS = {"memory"}


def validate_newgen_config(raw: object) -> dict[str, object]:
    # Validate the node-centric config structure (Configuration spec §2.1).
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    scenario = _require_mapping(raw, "scenario")
    scenario_name = scenario.get("name")
    if not isinstance(scenario_name, str) or not scenario_name:
        raise ConfigError("scenario.name must be a non-empty string")

    runtime = _optional_mapping(raw, "runtime")
    _normalize_runtime_platform(runtime)
    nodes = _optional_mapping(raw, "nodes")
    adapters = _optional_mapping(raw, "adapters")

    discovery_modules = runtime.get("discovery_modules", [])
    if not isinstance(discovery_modules, list) or not all(isinstance(item, str) for item in discovery_modules):
        raise ConfigError("runtime.discovery_modules must be a list of strings")

    output_sink = _require_mapping(adapters, "output_sink")
    if not isinstance(output_sink, dict):
        raise ConfigError("adapters.output_sink must be a mapping when provided")
    kind = output_sink.get("kind")
    factory = output_sink.get("factory")
    if kind is not None:
        raise ConfigError("adapters.output_sink.kind is not supported; use adapter name as YAML key")
    if factory is not None:
        raise ConfigError("adapters.output_sink.factory is not supported")
    binds = output_sink.get("binds", [])
    if not isinstance(binds, list):
        raise ConfigError("adapters.output_sink.binds must be a list")
    if not all(isinstance(item, str) for item in binds):
        raise ConfigError("adapters.output_sink.binds entries must be strings")
    unknown = [item for item in binds if item not in _STABLE_PORT_TYPES]
    if unknown:
        raise ConfigError(
            f"adapters.output_sink.binds entries must be one of: {sorted(_STABLE_PORT_TYPES)}"
        )
    settings = output_sink.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError("adapters.output_sink.settings must be a mapping when provided")

    # Normalize missing sections to keep downstream code simple.
    validated: dict[str, object] = dict(raw)
    validated.setdefault("runtime", runtime)
    validated.setdefault("nodes", nodes)
    validated.setdefault("adapters", adapters)
    return validated


def _require_mapping(root: dict[str, object], key: str) -> dict[str, object]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping when provided")
    return value


def _optional_mapping(root: dict[str, object], key: str) -> dict[str, object]:
    value = root.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping when provided")
    return value


def _normalize_runtime_platform(runtime: dict[str, object]) -> None:
    # Runtime platform defaults are centralized in validator to keep bootstrap deterministic.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ConfigError("runtime.platform must be a mapping when provided")
    runtime["platform"] = platform

    kv = platform.get("kv", {})
    if not isinstance(kv, dict):
        raise ConfigError("runtime.platform.kv must be a mapping when provided")
    platform["kv"] = kv

    backend = kv.get("backend", "memory")
    if not isinstance(backend, str) or not backend:
        raise ConfigError("runtime.platform.kv.backend must be a non-empty string when provided")
    if backend not in _SUPPORTED_KV_BACKENDS:
        raise ConfigError(
            f"runtime.platform.kv.backend must be one of: {sorted(_SUPPORTED_KV_BACKENDS)}"
        )
    kv["backend"] = backend
