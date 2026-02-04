from __future__ import annotations


class ConfigError(ValueError):
    # Raised for invalid framework config (fail fast).
    pass


def validate_newgen_config(raw: object) -> dict[str, object]:
    # Validate the node-centric config structure (Configuration spec §2.1).
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    scenario = _require_mapping(raw, "scenario")
    scenario_name = scenario.get("name")
    if not isinstance(scenario_name, str) or not scenario_name:
        raise ConfigError("scenario.name must be a non-empty string")

    runtime = _optional_mapping(raw, "runtime")
    nodes = _optional_mapping(raw, "nodes")
    adapters = _optional_mapping(raw, "adapters")

    discovery_modules = runtime.get("discovery_modules", [])
    if not isinstance(discovery_modules, list) or not all(isinstance(item, str) for item in discovery_modules):
        raise ConfigError("runtime.discovery_modules must be a list of strings")

    output_sink = _require_mapping(adapters, "output_sink")
    if not isinstance(output_sink, dict):
        raise ConfigError("adapters.output_sink must be a mapping when provided")
    factory = output_sink.get("factory")
    if not isinstance(factory, str) or not factory:
        raise ConfigError("adapters.output_sink.factory must be a non-empty string")
    binds = output_sink.get("binds", [])
    if not isinstance(binds, list):
        raise ConfigError("adapters.output_sink.binds must be a list")
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
