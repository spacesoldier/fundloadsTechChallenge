from __future__ import annotations


class ConfigError(ValueError):
    # Raised for invalid framework config (fail fast).
    pass


_STABLE_PORT_TYPES = {"stream", "kv_stream", "kv", "request", "response", "service"}
_SUPPORTED_KV_BACKENDS = {"memory"}
_SUPPORTED_FILE_FORMATS = {"text/jsonl", "text/plain", "application/octet-stream"}
_SUPPORTED_DECODE_ERROR_POLICIES = {"strict", "replace"}
_SUPPORTED_ORDERING_SINK_MODES = {"completion", "source_seq"}
_SUPPORTED_EXECUTION_IPC_TRANSPORTS = {"tcp_local"}
_SUPPORTED_EXECUTION_IPC_AUTH_MODES = {"hmac"}
_SUPPORTED_BOOTSTRAP_MODES = {"inline", "process_supervisor"}
_SUPPORTED_EXECUTION_IPC_SECRET_MODES = {"static", "generated"}
_SUPPORTED_EXECUTION_IPC_KDFS = {"none", "hkdf_sha256"}
_SUPPORTED_WEB_INTERFACE_KINDS = {"http", "http_stream", "websocket", "graphql"}
_SUPPORTED_WEB_BIND_PORT_TYPES = {"request", "response", "stream", "kv_stream"}
_PROCESS_GROUP_SELECTOR_KEYS = {"stages", "tags", "runners", "nodes"}
_SUPPORTED_RUNTIME_KEYS = {
    "strict",
    "discovery_modules",
    "platform",
    "ordering",
    "web",
    "tracing",
    "cli",
}


def validate_newgen_config(raw: object) -> dict[str, object]:
    # Validate the node-centric config structure (Configuration spec §2.1).
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    scenario = _require_mapping(raw, "scenario")
    scenario_name = scenario.get("name")
    if not isinstance(scenario_name, str) or not scenario_name:
        raise ConfigError("scenario.name must be a non-empty string")

    runtime = _optional_mapping(raw, "runtime")
    _validate_runtime_top_level_keys(runtime)
    _normalize_runtime_platform(runtime)
    _normalize_runtime_ordering(runtime)
    _normalize_runtime_web(runtime)
    _normalize_runtime_tracing(runtime)
    _normalize_runtime_cli(runtime)
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


def _validate_runtime_top_level_keys(runtime: dict[str, object]) -> None:
    unsupported = sorted(key for key in runtime if key not in _SUPPORTED_RUNTIME_KEYS)
    if unsupported:
        raise ConfigError(
            "runtime has unsupported keys: "
            f"{unsupported}. Allowed keys: {sorted(_SUPPORTED_RUNTIME_KEYS)}"
        )


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

    bootstrap = platform.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        raise ConfigError("runtime.platform.bootstrap must be a mapping when provided")
    platform["bootstrap"] = bootstrap
    bootstrap_mode = bootstrap.get("mode", "inline")
    if not isinstance(bootstrap_mode, str) or not bootstrap_mode:
        raise ConfigError("runtime.platform.bootstrap.mode must be a non-empty string when provided")
    if bootstrap_mode not in _SUPPORTED_BOOTSTRAP_MODES:
        raise ConfigError(
            "runtime.platform.bootstrap.mode must be one of: "
            f"{sorted(_SUPPORTED_BOOTSTRAP_MODES)}"
        )
    bootstrap["mode"] = bootstrap_mode

    execution_ipc = platform.get("execution_ipc")
    if execution_ipc is not None:
        if not isinstance(execution_ipc, dict):
            raise ConfigError("runtime.platform.execution_ipc must be a mapping when provided")

        transport = execution_ipc.get("transport", "tcp_local")
        if not isinstance(transport, str) or not transport:
            raise ConfigError("runtime.platform.execution_ipc.transport must be a non-empty string when provided")
        if transport not in _SUPPORTED_EXECUTION_IPC_TRANSPORTS:
            raise ConfigError(
                "runtime.platform.execution_ipc.transport must be one of: "
                f"{sorted(_SUPPORTED_EXECUTION_IPC_TRANSPORTS)}"
            )
        execution_ipc["transport"] = transport

        bind_host = execution_ipc.get("bind_host", "127.0.0.1")
        if not isinstance(bind_host, str) or not bind_host:
            raise ConfigError("runtime.platform.execution_ipc.bind_host must be a non-empty string when provided")
        if transport == "tcp_local" and bind_host != "127.0.0.1":
            raise ConfigError("runtime.platform.execution_ipc.bind_host must be 127.0.0.1 for tcp_local transport")
        execution_ipc["bind_host"] = bind_host

        bind_port = execution_ipc.get("bind_port", 0)
        if not isinstance(bind_port, int):
            raise ConfigError("runtime.platform.execution_ipc.bind_port must be an integer when provided")
        if bind_port < 0 or bind_port > 65535:
            raise ConfigError("runtime.platform.execution_ipc.bind_port must be in range [0, 65535]")
        execution_ipc["bind_port"] = bind_port

        auth = execution_ipc.get("auth", {})
        if not isinstance(auth, dict):
            raise ConfigError("runtime.platform.execution_ipc.auth must be a mapping when provided")
        execution_ipc["auth"] = auth

        auth_mode = auth.get("mode", "hmac")
        if not isinstance(auth_mode, str) or not auth_mode:
            raise ConfigError("runtime.platform.execution_ipc.auth.mode must be a non-empty string when provided")
        if auth_mode not in _SUPPORTED_EXECUTION_IPC_AUTH_MODES:
            raise ConfigError(
                "runtime.platform.execution_ipc.auth.mode must be one of: "
                f"{sorted(_SUPPORTED_EXECUTION_IPC_AUTH_MODES)}"
            )
        auth["mode"] = auth_mode

        secret_mode = auth.get("secret_mode", "static")
        if not isinstance(secret_mode, str) or not secret_mode:
            raise ConfigError(
                "runtime.platform.execution_ipc.auth.secret_mode must be a non-empty string when provided"
            )
        if secret_mode not in _SUPPORTED_EXECUTION_IPC_SECRET_MODES:
            raise ConfigError(
                "runtime.platform.execution_ipc.auth.secret_mode must be one of: "
                f"{sorted(_SUPPORTED_EXECUTION_IPC_SECRET_MODES)}"
            )
        auth["secret_mode"] = secret_mode

        default_kdf = "hkdf_sha256" if secret_mode == "generated" else "none"
        kdf = auth.get("kdf", default_kdf)
        if not isinstance(kdf, str) or not kdf:
            raise ConfigError("runtime.platform.execution_ipc.auth.kdf must be a non-empty string when provided")
        if kdf not in _SUPPORTED_EXECUTION_IPC_KDFS:
            raise ConfigError(
                "runtime.platform.execution_ipc.auth.kdf must be one of: "
                f"{sorted(_SUPPORTED_EXECUTION_IPC_KDFS)}"
            )
        auth["kdf"] = kdf

        ttl_seconds = auth.get("ttl_seconds", 30)
        if not isinstance(ttl_seconds, int):
            raise ConfigError("runtime.platform.execution_ipc.auth.ttl_seconds must be an integer when provided")
        if ttl_seconds <= 0:
            raise ConfigError("runtime.platform.execution_ipc.auth.ttl_seconds must be > 0")
        auth["ttl_seconds"] = ttl_seconds

        nonce_cache_size = auth.get("nonce_cache_size", 100000)
        if not isinstance(nonce_cache_size, int):
            raise ConfigError(
                "runtime.platform.execution_ipc.auth.nonce_cache_size must be an integer when provided"
            )
        if nonce_cache_size <= 0:
            raise ConfigError("runtime.platform.execution_ipc.auth.nonce_cache_size must be > 0")
        auth["nonce_cache_size"] = nonce_cache_size

        max_payload_bytes = execution_ipc.get("max_payload_bytes", 1048576)
        if not isinstance(max_payload_bytes, int):
            raise ConfigError("runtime.platform.execution_ipc.max_payload_bytes must be an integer when provided")
        if max_payload_bytes <= 0:
            raise ConfigError("runtime.platform.execution_ipc.max_payload_bytes must be > 0")
        execution_ipc["max_payload_bytes"] = max_payload_bytes

    if bootstrap_mode == "process_supervisor":
        if execution_ipc is None:
            raise ConfigError(
                "runtime.platform.bootstrap.mode=process_supervisor requires runtime.platform.execution_ipc"
            )
        transport = execution_ipc.get("transport")
        if transport != "tcp_local":
            raise ConfigError(
                "runtime.platform.bootstrap.mode=process_supervisor requires "
                "runtime.platform.execution_ipc.transport=tcp_local"
            )

    process_groups = platform.get("process_groups")
    if process_groups is not None:
        if not isinstance(process_groups, list):
            raise ConfigError("runtime.platform.process_groups must be a list when provided")

        seen_names: set[str] = set()
        for index, group in enumerate(process_groups):
            if not isinstance(group, dict):
                raise ConfigError(f"runtime.platform.process_groups[{index}] must be a mapping")

            name = group.get("name")
            if not isinstance(name, str) or not name:
                raise ConfigError(f"runtime.platform.process_groups[{index}].name must be a non-empty string")
            if name in seen_names:
                raise ConfigError(f"runtime.platform.process_groups contains duplicate name: {name}")
            seen_names.add(name)

            allowed = _PROCESS_GROUP_SELECTOR_KEYS | {"name"}
            unknown_keys = [key for key in group if key not in allowed]
            if unknown_keys:
                raise ConfigError(
                    f"runtime.platform.process_groups[{index}] has unsupported keys: {sorted(unknown_keys)}"
                )

            for key in _PROCESS_GROUP_SELECTOR_KEYS:
                value = group.get(key)
                if value is None:
                    continue
                if not isinstance(value, list):
                    raise ConfigError(f"runtime.platform.process_groups[{index}].{key} must be a list when provided")
                if not all(isinstance(item, str) and item for item in value):
                    raise ConfigError(
                        f"runtime.platform.process_groups[{index}].{key} entries must be non-empty strings"
                    )


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


def _normalize_runtime_web(runtime: dict[str, object]) -> None:
    # Runtime web contract is validated here to keep web/execution split deterministic.
    web = runtime.get("web")
    if web is None:
        return
    if not isinstance(web, dict):
        raise ConfigError("runtime.web must be a mapping when provided")
    runtime["web"] = web

    interfaces = web.get("interfaces", [])
    if not isinstance(interfaces, list):
        raise ConfigError("runtime.web.interfaces must be a list when provided")
    web["interfaces"] = interfaces

    for index, interface in enumerate(interfaces):
        if not isinstance(interface, dict):
            raise ConfigError(f"runtime.web.interfaces[{index}] must be a mapping")

        kind = interface.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ConfigError(f"runtime.web.interfaces[{index}].kind must be a non-empty string")
        if kind not in _SUPPORTED_WEB_INTERFACE_KINDS:
            raise ConfigError(
                "runtime.web.interfaces["
                f"{index}].kind must be one of: {sorted(_SUPPORTED_WEB_INTERFACE_KINDS)}"
            )

        binds = interface.get("binds", [])
        if not isinstance(binds, list):
            raise ConfigError(f"runtime.web.interfaces[{index}].binds must be a list when provided")
        if not all(isinstance(item, str) for item in binds):
            raise ConfigError(f"runtime.web.interfaces[{index}].binds entries must be strings")
        unknown = [item for item in binds if item not in _SUPPORTED_WEB_BIND_PORT_TYPES]
        if unknown:
            raise ConfigError(
                f"runtime.web.interfaces[{index}].binds entries must be one of: "
                f"{sorted(_SUPPORTED_WEB_BIND_PORT_TYPES)}"
            )


def _normalize_runtime_tracing(runtime: dict[str, object]) -> None:
    # Runtime tracing section is optional and validated as a mapping when provided.
    tracing = runtime.get("tracing")
    if tracing is None:
        return
    if not isinstance(tracing, dict):
        raise ConfigError("runtime.tracing must be a mapping when provided")
    runtime["tracing"] = tracing


def _normalize_runtime_cli(runtime: dict[str, object]) -> None:
    # Runtime CLI section is optional and validated as a mapping when provided.
    cli = runtime.get("cli")
    if cli is None:
        return
    if not isinstance(cli, dict):
        raise ConfigError("runtime.cli must be a mapping when provided")
    runtime["cli"] = cli
