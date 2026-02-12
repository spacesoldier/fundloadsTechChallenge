from __future__ import annotations


class ConfigError(ValueError):
    # Raised for invalid framework config (fail fast).
    pass


_STABLE_PORT_TYPES = {"stream", "kv_stream", "kv", "request", "response", "service"}
_SUPPORTED_KV_BACKENDS = {"memory"}
_SUPPORTED_FILE_FORMATS = {"text/jsonl", "text/plain", "application/octet-stream"}
_SUPPORTED_DECODE_ERROR_POLICIES = {"strict", "replace"}
_SUPPORTED_ORDERING_SINK_MODES = {"completion", "source_seq"}


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
    _normalize_runtime_ordering(runtime)
    nodes = _optional_mapping(raw, "nodes")
    adapters = _optional_mapping(raw, "adapters")

    discovery_modules = runtime.get("discovery_modules", [])
    if not isinstance(discovery_modules, list) or not all(isinstance(item, str) for item in discovery_modules):
        raise ConfigError("runtime.discovery_modules must be a list of strings")

    for role, entry in adapters.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"adapters.{role} must be a mapping when provided")
        kind = entry.get("kind")
        factory = entry.get("factory")
        if kind is not None:
            raise ConfigError(
                f"adapters.{role}.kind is not supported; use adapter name as YAML key"
            )
        if factory is not None:
            raise ConfigError(f"adapters.{role}.factory is not supported")
        binds = entry.get("binds", [])
        if not isinstance(binds, list):
            raise ConfigError(f"adapters.{role}.binds must be a list")
        if not all(isinstance(item, str) for item in binds):
            raise ConfigError(f"adapters.{role}.binds entries must be strings")
        unknown = [item for item in binds if item not in _STABLE_PORT_TYPES]
        if unknown:
            raise ConfigError(
                f"adapters.{role}.binds entries must be one of: {sorted(_STABLE_PORT_TYPES)}"
            )
        settings = entry.get("settings", {})
        if not isinstance(settings, dict):
            raise ConfigError(f"adapters.{role}.settings must be a mapping when provided")
        fmt = settings.get("format")
        if fmt is not None:
            if not isinstance(fmt, str) or not fmt:
                raise ConfigError(f"adapters.{role}.settings.format must be a non-empty string when provided")
            if fmt not in _SUPPORTED_FILE_FORMATS:
                raise ConfigError(
                    f"adapters.{role}.settings.format must be one of: {sorted(_SUPPORTED_FILE_FORMATS)}"
                )
        decode_errors = settings.get("decode_errors")
        if decode_errors is not None:
            if not isinstance(decode_errors, str) or not decode_errors:
                raise ConfigError(
                    f"adapters.{role}.settings.decode_errors must be a non-empty string when provided"
                )
            if decode_errors not in _SUPPORTED_DECODE_ERROR_POLICIES:
                raise ConfigError(
                    "adapters."
                    f"{role}.settings.decode_errors must be one of: {sorted(_SUPPORTED_DECODE_ERROR_POLICIES)}"
                )
        encoding = settings.get("encoding")
        if encoding is not None:
            if not isinstance(encoding, str) or not encoding:
                raise ConfigError(
                    f"adapters.{role}.settings.encoding must be a non-empty string when provided"
                )

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


def _normalize_runtime_ordering(runtime: dict[str, object]) -> None:
    # Runtime ordering defaults are centralized in validator for deterministic runner behavior.
    ordering = runtime.get("ordering", {})
    if not isinstance(ordering, dict):
        raise ConfigError("runtime.ordering must be a mapping when provided")
    runtime["ordering"] = ordering

    sink_mode = ordering.get("sink_mode", "completion")
    if not isinstance(sink_mode, str) or not sink_mode:
        raise ConfigError("runtime.ordering.sink_mode must be a non-empty string when provided")
    if sink_mode not in _SUPPORTED_ORDERING_SINK_MODES:
        raise ConfigError(
            "runtime.ordering.sink_mode must be one of: "
            f"{sorted(_SUPPORTED_ORDERING_SINK_MODES)}"
        )
    ordering["sink_mode"] = sink_mode
