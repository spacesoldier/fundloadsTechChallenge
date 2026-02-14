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
_PROCESS_GROUP_RUNTIME_KEYS = {
    "workers",
    "runner_profile",
    "services",
    "heartbeat_seconds",
    "start_timeout_seconds",
    "stop_timeout_seconds",
}
_SUPPORTED_PROCESS_GROUP_RUNNER_PROFILES = {"sync", "async"}
_SUPPORTED_PROCESS_GROUP_SERVICE_KEYS = {"api_service_profile", "rate_limiter_profile"}
_SUPPORTED_OBSERVABILITY_TRACE_EXPORTER_KINDS = {"jsonl", "stdout", "otel_otlp", "opentracing_bridge"}
_SUPPORTED_OBSERVABILITY_LOG_EXPORTER_KINDS = {"stdout", "jsonl", "otel_logs_otlp"}
_SUPPORTED_OBSERVABILITY_LOG_LEVELS = {"info", "debug"}
_SUPPORTED_API_POLICY_KEYS = {"defaults", "profiles"}
_SUPPORTED_API_POLICY_DEFAULT_KEYS = {
    "timeout_ms",
    "retry",
    "circuit_breaker",
    "auth",
    "telemetry",
    "batching",
    "rate_limit",
    "execution_mode",
}
_SUPPORTED_API_POLICY_EXECUTION_MODES = {"sync", "async", "any"}
_SUPPORTED_API_POLICY_RETRY_KEYS = {"max_attempts", "backoff_ms"}
_SUPPORTED_API_POLICY_CIRCUIT_BREAKER_KEYS = {"failure_threshold", "reset_timeout_ms", "half_open_max_calls"}
_SUPPORTED_API_POLICY_BATCHING_KEYS = {"max_items", "flush_interval_ms"}
_SUPPORTED_RATE_LIMIT_KINDS = {
    "fixed_window",
    "sliding_window_counter",
    "sliding_window_log",
    "token_bucket",
    "leaky_bucket",
    "concurrency",
}
_SUPPORTED_WEB_INTERFACE_POLICY_KEYS = {"rate_limit", "request_size_bytes", "timeout_ms"}
_SUPPORTED_RUNTIME_KEYS = {
    "strict",
    "discovery_modules",
    "platform",
    "ordering",
    "web",
    "observability",
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
    _normalize_runtime_observability(runtime)
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

    api_policies = platform.get("api_policies")
    if api_policies is not None:
        if not isinstance(api_policies, dict):
            raise ConfigError("runtime.platform.api_policies must be a mapping when provided")
        _normalize_api_policies(api_policies, prefix="runtime.platform.api_policies")
        platform["api_policies"] = api_policies

    execution_ipc = platform.get("execution_ipc")
    if execution_ipc is not None:
        if not isinstance(execution_ipc, dict):
            raise ConfigError("runtime.platform.execution_ipc must be a mapping when provided")
        _normalize_execution_ipc_mapping(
            execution_ipc,
            prefix="runtime.platform.execution_ipc",
        )

        control = execution_ipc.get("control")
        if control is not None:
            if not isinstance(control, dict):
                raise ConfigError("runtime.platform.execution_ipc.control must be a mapping when provided")
            _normalize_execution_ipc_mapping(
                control,
                prefix="runtime.platform.execution_ipc.control",
            )

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

            allowed = _PROCESS_GROUP_SELECTOR_KEYS | _PROCESS_GROUP_RUNTIME_KEYS | {"name"}
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

            workers = group.get("workers", 1)
            if not isinstance(workers, int):
                raise ConfigError(f"runtime.platform.process_groups[{index}].workers must be an integer when provided")
            if workers <= 0:
                raise ConfigError(f"runtime.platform.process_groups[{index}].workers must be > 0")
            group["workers"] = workers

            runner_profile = group.get("runner_profile", "sync")
            if not isinstance(runner_profile, str) or not runner_profile:
                raise ConfigError(
                    f"runtime.platform.process_groups[{index}].runner_profile must be a non-empty string when provided"
                )
            if runner_profile not in _SUPPORTED_PROCESS_GROUP_RUNNER_PROFILES:
                raise ConfigError(
                    "runtime.platform.process_groups["
                    f"{index}].runner_profile must be one of: {sorted(_SUPPORTED_PROCESS_GROUP_RUNNER_PROFILES)}"
                )
            group["runner_profile"] = runner_profile

            services = group.get("services")
            if services is not None:
                if not isinstance(services, dict):
                    raise ConfigError(
                        f"runtime.platform.process_groups[{index}].services must be a mapping when provided"
                    )
                unknown_service_keys = sorted(key for key in services if key not in _SUPPORTED_PROCESS_GROUP_SERVICE_KEYS)
                if unknown_service_keys:
                    raise ConfigError(
                        "runtime.platform.process_groups["
                        f"{index}].services has unsupported keys: {unknown_service_keys}"
                    )
                for key in _SUPPORTED_PROCESS_GROUP_SERVICE_KEYS:
                    value = services.get(key)
                    if value is None:
                        continue
                    if not isinstance(value, str) or not value:
                        raise ConfigError(
                            "runtime.platform.process_groups["
                            f"{index}].services.{key} must be a non-empty string when provided"
                        )
                group["services"] = services

            heartbeat_seconds = group.get("heartbeat_seconds", 5)
            if not isinstance(heartbeat_seconds, int):
                raise ConfigError(
                    f"runtime.platform.process_groups[{index}].heartbeat_seconds must be an integer when provided"
                )
            if heartbeat_seconds <= 0:
                raise ConfigError(f"runtime.platform.process_groups[{index}].heartbeat_seconds must be > 0")
            group["heartbeat_seconds"] = heartbeat_seconds

            start_timeout_seconds = group.get("start_timeout_seconds", 30)
            if not isinstance(start_timeout_seconds, int):
                raise ConfigError(
                    f"runtime.platform.process_groups[{index}].start_timeout_seconds must be an integer when provided"
                )
            if start_timeout_seconds <= 0:
                raise ConfigError(f"runtime.platform.process_groups[{index}].start_timeout_seconds must be > 0")
            group["start_timeout_seconds"] = start_timeout_seconds

            stop_timeout_seconds = group.get("stop_timeout_seconds", 30)
            if not isinstance(stop_timeout_seconds, int):
                raise ConfigError(
                    f"runtime.platform.process_groups[{index}].stop_timeout_seconds must be an integer when provided"
                )
            if stop_timeout_seconds <= 0:
                raise ConfigError(f"runtime.platform.process_groups[{index}].stop_timeout_seconds must be > 0")
            group["stop_timeout_seconds"] = stop_timeout_seconds

    readiness = platform.get("readiness")
    if readiness is not None:
        if not isinstance(readiness, dict):
            raise ConfigError("runtime.platform.readiness must be a mapping when provided")
        enabled = readiness.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError("runtime.platform.readiness.enabled must be a boolean when provided")
        readiness["enabled"] = enabled

        start_work_on_all_groups_ready = readiness.get("start_work_on_all_groups_ready", True)
        if not isinstance(start_work_on_all_groups_ready, bool):
            raise ConfigError(
                "runtime.platform.readiness.start_work_on_all_groups_ready must be a boolean when provided"
            )
        readiness["start_work_on_all_groups_ready"] = start_work_on_all_groups_ready

        readiness_timeout_seconds = readiness.get("readiness_timeout_seconds", 30)
        if not isinstance(readiness_timeout_seconds, int):
            raise ConfigError(
                "runtime.platform.readiness.readiness_timeout_seconds must be an integer when provided"
            )
        if readiness_timeout_seconds <= 0:
            raise ConfigError("runtime.platform.readiness.readiness_timeout_seconds must be > 0")
        readiness["readiness_timeout_seconds"] = readiness_timeout_seconds

    routing_cache = platform.get("routing_cache")
    if routing_cache is not None:
        if not isinstance(routing_cache, dict):
            raise ConfigError("runtime.platform.routing_cache must be a mapping when provided")
        unknown_keys = [key for key in routing_cache if key not in {"enabled", "negative_cache", "max_entries"}]
        if unknown_keys:
            raise ConfigError(
                "runtime.platform.routing_cache has unsupported keys: "
                f"{sorted(unknown_keys)}"
            )
        enabled = routing_cache.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError("runtime.platform.routing_cache.enabled must be a boolean when provided")
        routing_cache["enabled"] = enabled

        negative_cache = routing_cache.get("negative_cache", True)
        if not isinstance(negative_cache, bool):
            raise ConfigError("runtime.platform.routing_cache.negative_cache must be a boolean when provided")
        routing_cache["negative_cache"] = negative_cache

        max_entries = routing_cache.get("max_entries", 100000)
        if not isinstance(max_entries, int):
            raise ConfigError("runtime.platform.routing_cache.max_entries must be an integer when provided")
        if max_entries <= 0:
            raise ConfigError("runtime.platform.routing_cache.max_entries must be > 0")
        routing_cache["max_entries"] = max_entries


def _normalize_execution_ipc_mapping(mapping: dict[str, object], *, prefix: str) -> None:
    transport = mapping.get("transport", "tcp_local")
    if not isinstance(transport, str) or not transport:
        raise ConfigError(f"{prefix}.transport must be a non-empty string when provided")
    if transport not in _SUPPORTED_EXECUTION_IPC_TRANSPORTS:
        raise ConfigError(
            f"{prefix}.transport must be one of: {sorted(_SUPPORTED_EXECUTION_IPC_TRANSPORTS)}"
        )
    mapping["transport"] = transport

    bind_host = mapping.get("bind_host", "127.0.0.1")
    if not isinstance(bind_host, str) or not bind_host:
        raise ConfigError(f"{prefix}.bind_host must be a non-empty string when provided")
    if transport == "tcp_local" and bind_host != "127.0.0.1":
        raise ConfigError(f"{prefix}.bind_host must be 127.0.0.1 for tcp_local transport")
    mapping["bind_host"] = bind_host

    bind_port = mapping.get("bind_port", 0)
    if not isinstance(bind_port, int):
        raise ConfigError(f"{prefix}.bind_port must be an integer when provided")
    if bind_port < 0 or bind_port > 65535:
        raise ConfigError(f"{prefix}.bind_port must be in range [0, 65535]")
    mapping["bind_port"] = bind_port

    auth = mapping.get("auth", {})
    if not isinstance(auth, dict):
        raise ConfigError(f"{prefix}.auth must be a mapping when provided")
    mapping["auth"] = auth

    auth_mode = auth.get("mode", "hmac")
    if not isinstance(auth_mode, str) or not auth_mode:
        raise ConfigError(f"{prefix}.auth.mode must be a non-empty string when provided")
    if auth_mode not in _SUPPORTED_EXECUTION_IPC_AUTH_MODES:
        raise ConfigError(f"{prefix}.auth.mode must be one of: {sorted(_SUPPORTED_EXECUTION_IPC_AUTH_MODES)}")
    auth["mode"] = auth_mode

    secret_mode = auth.get("secret_mode", "static")
    if not isinstance(secret_mode, str) or not secret_mode:
        raise ConfigError(f"{prefix}.auth.secret_mode must be a non-empty string when provided")
    if secret_mode not in _SUPPORTED_EXECUTION_IPC_SECRET_MODES:
        raise ConfigError(
            f"{prefix}.auth.secret_mode must be one of: {sorted(_SUPPORTED_EXECUTION_IPC_SECRET_MODES)}"
        )
    auth["secret_mode"] = secret_mode

    default_kdf = "hkdf_sha256" if secret_mode == "generated" else "none"
    kdf = auth.get("kdf", default_kdf)
    if not isinstance(kdf, str) or not kdf:
        raise ConfigError(f"{prefix}.auth.kdf must be a non-empty string when provided")
    if kdf not in _SUPPORTED_EXECUTION_IPC_KDFS:
        raise ConfigError(f"{prefix}.auth.kdf must be one of: {sorted(_SUPPORTED_EXECUTION_IPC_KDFS)}")
    auth["kdf"] = kdf

    ttl_seconds = auth.get("ttl_seconds", 30)
    if not isinstance(ttl_seconds, int):
        raise ConfigError(f"{prefix}.auth.ttl_seconds must be an integer when provided")
    if ttl_seconds <= 0:
        raise ConfigError(f"{prefix}.auth.ttl_seconds must be > 0")
    auth["ttl_seconds"] = ttl_seconds

    nonce_cache_size = auth.get("nonce_cache_size", 100000)
    if not isinstance(nonce_cache_size, int):
        raise ConfigError(f"{prefix}.auth.nonce_cache_size must be an integer when provided")
    if nonce_cache_size <= 0:
        raise ConfigError(f"{prefix}.auth.nonce_cache_size must be > 0")
    auth["nonce_cache_size"] = nonce_cache_size

    max_payload_bytes = mapping.get("max_payload_bytes", 1048576)
    if not isinstance(max_payload_bytes, int):
        raise ConfigError(f"{prefix}.max_payload_bytes must be an integer when provided")
    if max_payload_bytes <= 0:
        raise ConfigError(f"{prefix}.max_payload_bytes must be > 0")
    mapping["max_payload_bytes"] = max_payload_bytes


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

        policies = interface.get("policies")
        if policies is not None:
            if not isinstance(policies, dict):
                raise ConfigError(f"runtime.web.interfaces[{index}].policies must be a mapping when provided")
            _normalize_web_interface_policies(
                policies,
                prefix=f"runtime.web.interfaces[{index}].policies",
            )
            interface["policies"] = policies


def _normalize_runtime_observability(runtime: dict[str, object]) -> None:
    observability = runtime.get("observability")
    if observability is None:
        return
    if not isinstance(observability, dict):
        raise ConfigError("runtime.observability must be a mapping when provided")
    runtime["observability"] = observability

    tracing = observability.get("tracing", {})
    if not isinstance(tracing, dict):
        raise ConfigError("runtime.observability.tracing must be a mapping when provided")
    observability["tracing"] = tracing
    tracing_exporters = tracing.get("exporters", [])
    if not isinstance(tracing_exporters, list):
        raise ConfigError("runtime.observability.tracing.exporters must be a list when provided")
    for index, exporter in enumerate(tracing_exporters):
        if not isinstance(exporter, dict):
            raise ConfigError(f"runtime.observability.tracing.exporters[{index}] must be a mapping")
        kind = exporter.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ConfigError(f"runtime.observability.tracing.exporters[{index}].kind must be a non-empty string")
        if kind not in _SUPPORTED_OBSERVABILITY_TRACE_EXPORTER_KINDS:
            raise ConfigError(
                "runtime.observability.tracing.exporters["
                f"{index}].kind must be one of: {sorted(_SUPPORTED_OBSERVABILITY_TRACE_EXPORTER_KINDS)}"
            )
        settings = exporter.get("settings", {})
        if not isinstance(settings, dict):
            raise ConfigError(
                f"runtime.observability.tracing.exporters[{index}].settings must be a mapping when provided"
            )
        exporter["settings"] = settings

    logging = observability.get("logging", {})
    if not isinstance(logging, dict):
        raise ConfigError("runtime.observability.logging must be a mapping when provided")
    observability["logging"] = logging
    log_exporters = logging.get("exporters", [])
    if not isinstance(log_exporters, list):
        raise ConfigError("runtime.observability.logging.exporters must be a list when provided")
    for index, exporter in enumerate(log_exporters):
        if not isinstance(exporter, dict):
            raise ConfigError(f"runtime.observability.logging.exporters[{index}] must be a mapping")
        kind = exporter.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ConfigError(f"runtime.observability.logging.exporters[{index}].kind must be a non-empty string")
        if kind not in _SUPPORTED_OBSERVABILITY_LOG_EXPORTER_KINDS:
            raise ConfigError(
                "runtime.observability.logging.exporters["
                f"{index}].kind must be one of: {sorted(_SUPPORTED_OBSERVABILITY_LOG_EXPORTER_KINDS)}"
            )
        settings = exporter.get("settings", {})
        if not isinstance(settings, dict):
            raise ConfigError(
                f"runtime.observability.logging.exporters[{index}].settings must be a mapping when provided"
            )
        if kind == "jsonl":
            path = settings.get("path")
            if not isinstance(path, str) or not path:
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.path "
                    "must be a non-empty string for kind 'jsonl'"
                )
            workers_dir = settings.get("workers_dir")
            if workers_dir is not None and (not isinstance(workers_dir, str) or not workers_dir):
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.workers_dir "
                    "must be a non-empty string when provided"
                )
        exporter["settings"] = settings

    lifecycle_events = logging.get("lifecycle_events", {})
    if not isinstance(lifecycle_events, dict):
        raise ConfigError("runtime.observability.logging.lifecycle_events must be a mapping when provided")
    logging["lifecycle_events"] = lifecycle_events
    enabled = lifecycle_events.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("runtime.observability.logging.lifecycle_events.enabled must be a boolean when provided")
    lifecycle_events["enabled"] = enabled
    level = lifecycle_events.get("level", "info")
    if not isinstance(level, str) or not level:
        raise ConfigError("runtime.observability.logging.lifecycle_events.level must be a non-empty string")
    if level not in _SUPPORTED_OBSERVABILITY_LOG_LEVELS:
        raise ConfigError(
            "runtime.observability.logging.lifecycle_events.level must be one of: "
            f"{sorted(_SUPPORTED_OBSERVABILITY_LOG_LEVELS)}"
        )
    lifecycle_events["level"] = level


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


def _normalize_api_policies(
    api_policies: dict[str, object],
    *,
    prefix: str,
) -> None:
    unknown_keys = sorted(key for key in api_policies if key not in _SUPPORTED_API_POLICY_KEYS)
    if unknown_keys:
        raise ConfigError(
            f"{prefix} has unsupported keys: {unknown_keys}"
        )

    defaults = api_policies.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ConfigError(f"{prefix}.defaults must be a mapping when provided")
    _normalize_api_policy_profile(defaults, prefix=f"{prefix}.defaults")
    api_policies["defaults"] = defaults

    profiles = api_policies.get("profiles", {})
    if profiles is None:
        profiles = {}
    if not isinstance(profiles, dict):
        raise ConfigError(f"{prefix}.profiles must be a mapping when provided")
    normalized_profiles: dict[str, object] = {}
    for profile_name, profile in profiles.items():
        if not isinstance(profile_name, str) or not profile_name:
            raise ConfigError(f"{prefix}.profiles keys must be non-empty strings")
        if not isinstance(profile, dict):
            raise ConfigError(f"{prefix}.profiles.{profile_name} must be a mapping")
        _normalize_api_policy_profile(
            profile,
            prefix=f"{prefix}.profiles.{profile_name}",
        )
        normalized_profiles[profile_name] = profile
    api_policies["profiles"] = normalized_profiles


def _normalize_api_policy_profile(
    profile: dict[str, object],
    *,
    prefix: str,
) -> None:
    unknown_keys = sorted(key for key in profile if key not in _SUPPORTED_API_POLICY_DEFAULT_KEYS)
    if unknown_keys:
        raise ConfigError(
            f"{prefix} has unsupported keys: {unknown_keys}"
        )

    if "timeout_ms" in profile:
        timeout_ms = profile.get("timeout_ms")
        if not isinstance(timeout_ms, int):
            raise ConfigError(f"{prefix}.timeout_ms must be an integer when provided")
        if timeout_ms <= 0:
            raise ConfigError(f"{prefix}.timeout_ms must be > 0")
        profile["timeout_ms"] = timeout_ms

    if "execution_mode" in profile:
        execution_mode = profile.get("execution_mode")
        if not isinstance(execution_mode, str) or not execution_mode:
            raise ConfigError(f"{prefix}.execution_mode must be a non-empty string when provided")
        if execution_mode not in _SUPPORTED_API_POLICY_EXECUTION_MODES:
            raise ConfigError(
                f"{prefix}.execution_mode must be one of: {sorted(_SUPPORTED_API_POLICY_EXECUTION_MODES)}"
            )
        profile["execution_mode"] = execution_mode

    if "retry" in profile:
        retry = profile.get("retry")
        if not isinstance(retry, dict):
            raise ConfigError(f"{prefix}.retry must be a mapping when provided")
        unknown_retry = sorted(key for key in retry if key not in _SUPPORTED_API_POLICY_RETRY_KEYS)
        if unknown_retry:
            raise ConfigError(
                f"{prefix}.retry has unsupported keys: {unknown_retry}"
            )
        max_attempts = retry.get("max_attempts", 0)
        if not isinstance(max_attempts, int):
            raise ConfigError(f"{prefix}.retry.max_attempts must be an integer when provided")
        if max_attempts < 0:
            raise ConfigError(f"{prefix}.retry.max_attempts must be >= 0")
        retry["max_attempts"] = max_attempts
        backoff_ms = retry.get("backoff_ms", 0)
        if not isinstance(backoff_ms, int):
            raise ConfigError(f"{prefix}.retry.backoff_ms must be an integer when provided")
        if backoff_ms < 0:
            raise ConfigError(f"{prefix}.retry.backoff_ms must be >= 0")
        retry["backoff_ms"] = backoff_ms
        profile["retry"] = retry

    if "circuit_breaker" in profile:
        breaker = profile.get("circuit_breaker")
        if not isinstance(breaker, dict):
            raise ConfigError(f"{prefix}.circuit_breaker must be a mapping when provided")
        unknown_breaker = sorted(key for key in breaker if key not in _SUPPORTED_API_POLICY_CIRCUIT_BREAKER_KEYS)
        if unknown_breaker:
            raise ConfigError(
                f"{prefix}.circuit_breaker has unsupported keys: {unknown_breaker}"
            )
        failure_threshold = breaker.get("failure_threshold", 5)
        if not isinstance(failure_threshold, int):
            raise ConfigError(f"{prefix}.circuit_breaker.failure_threshold must be an integer when provided")
        if failure_threshold <= 0:
            raise ConfigError(f"{prefix}.circuit_breaker.failure_threshold must be > 0")
        breaker["failure_threshold"] = failure_threshold
        reset_timeout_ms = breaker.get("reset_timeout_ms", 30000)
        if not isinstance(reset_timeout_ms, int):
            raise ConfigError(f"{prefix}.circuit_breaker.reset_timeout_ms must be an integer when provided")
        if reset_timeout_ms <= 0:
            raise ConfigError(f"{prefix}.circuit_breaker.reset_timeout_ms must be > 0")
        breaker["reset_timeout_ms"] = reset_timeout_ms
        half_open_max_calls = breaker.get("half_open_max_calls", 1)
        if not isinstance(half_open_max_calls, int):
            raise ConfigError(f"{prefix}.circuit_breaker.half_open_max_calls must be an integer when provided")
        if half_open_max_calls <= 0:
            raise ConfigError(f"{prefix}.circuit_breaker.half_open_max_calls must be > 0")
        breaker["half_open_max_calls"] = half_open_max_calls
        profile["circuit_breaker"] = breaker

    for key in {"auth", "telemetry"}:
        if key not in profile:
            continue
        value = profile.get(key)
        if not isinstance(value, dict):
            raise ConfigError(f"{prefix}.{key} must be a mapping when provided")
        profile[key] = value

    if "batching" in profile:
        batching = profile.get("batching")
        if not isinstance(batching, dict):
            raise ConfigError(f"{prefix}.batching must be a mapping when provided")
        unknown_batching = sorted(key for key in batching if key not in _SUPPORTED_API_POLICY_BATCHING_KEYS)
        if unknown_batching:
            raise ConfigError(
                f"{prefix}.batching has unsupported keys: {unknown_batching}"
            )
        max_items = batching.get("max_items", 100)
        if not isinstance(max_items, int):
            raise ConfigError(f"{prefix}.batching.max_items must be an integer when provided")
        if max_items <= 0:
            raise ConfigError(f"{prefix}.batching.max_items must be > 0")
        batching["max_items"] = max_items
        flush_interval_ms = batching.get("flush_interval_ms", 1000)
        if not isinstance(flush_interval_ms, int):
            raise ConfigError(f"{prefix}.batching.flush_interval_ms must be an integer when provided")
        if flush_interval_ms <= 0:
            raise ConfigError(f"{prefix}.batching.flush_interval_ms must be > 0")
        batching["flush_interval_ms"] = flush_interval_ms
        profile["batching"] = batching

    if "rate_limit" in profile:
        rate_limit = profile.get("rate_limit")
        if not isinstance(rate_limit, dict):
            raise ConfigError(f"{prefix}.rate_limit must be a mapping when provided")
        _normalize_rate_limit_policy(rate_limit, prefix=f"{prefix}.rate_limit")
        profile["rate_limit"] = rate_limit


def _normalize_rate_limit_policy(
    rate_limit: dict[str, object],
    *,
    prefix: str,
) -> None:
    kind = rate_limit.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ConfigError(f"{prefix}.kind must be a non-empty string")
    if kind not in _SUPPORTED_RATE_LIMIT_KINDS:
        raise ConfigError(
            f"{prefix}.kind must be one of: {sorted(_SUPPORTED_RATE_LIMIT_KINDS)}"
        )
    rate_limit["kind"] = kind

    scope = rate_limit.get("scope")
    if scope is not None:
        if not isinstance(scope, str) or not scope:
            raise ConfigError(f"{prefix}.scope must be a non-empty string when provided")
        rate_limit["scope"] = scope

    if kind in {"fixed_window", "sliding_window_counter", "sliding_window_log"}:
        unknown_keys = sorted(key for key in rate_limit if key not in {"kind", "scope", "limit", "window_ms"})
        if unknown_keys:
            raise ConfigError(f"{prefix} has unsupported keys for '{kind}': {unknown_keys}")
        limit = rate_limit.get("limit")
        if not isinstance(limit, int):
            raise ConfigError(f"{prefix}.limit must be an integer for '{kind}'")
        if limit <= 0:
            raise ConfigError(f"{prefix}.limit must be > 0 for '{kind}'")
        rate_limit["limit"] = limit
        window_ms = rate_limit.get("window_ms")
        if not isinstance(window_ms, int):
            raise ConfigError(f"{prefix}.window_ms must be an integer for '{kind}'")
        if window_ms <= 0:
            raise ConfigError(f"{prefix}.window_ms must be > 0 for '{kind}'")
        rate_limit["window_ms"] = window_ms
        return

    if kind in {"token_bucket", "leaky_bucket"}:
        unknown_keys = sorted(
            key for key in rate_limit if key not in {"kind", "scope", "refill_rate_per_sec", "bucket_capacity"}
        )
        if unknown_keys:
            raise ConfigError(f"{prefix} has unsupported keys for '{kind}': {unknown_keys}")
        refill_rate = rate_limit.get("refill_rate_per_sec")
        if not isinstance(refill_rate, (int, float)):
            raise ConfigError(f"{prefix}.refill_rate_per_sec must be numeric for '{kind}'")
        if float(refill_rate) <= 0:
            raise ConfigError(f"{prefix}.refill_rate_per_sec must be > 0 for '{kind}'")
        rate_limit["refill_rate_per_sec"] = float(refill_rate)
        bucket_capacity = rate_limit.get("bucket_capacity")
        if not isinstance(bucket_capacity, int):
            raise ConfigError(f"{prefix}.bucket_capacity must be an integer for '{kind}'")
        if bucket_capacity <= 0:
            raise ConfigError(f"{prefix}.bucket_capacity must be > 0 for '{kind}'")
        rate_limit["bucket_capacity"] = bucket_capacity
        return

    unknown_keys = sorted(key for key in rate_limit if key not in {"kind", "scope", "max_in_flight"})
    if unknown_keys:
        raise ConfigError(f"{prefix} has unsupported keys for 'concurrency': {unknown_keys}")
    max_in_flight = rate_limit.get("max_in_flight")
    if not isinstance(max_in_flight, int):
        raise ConfigError(f"{prefix}.max_in_flight must be an integer for 'concurrency'")
    if max_in_flight <= 0:
        raise ConfigError(f"{prefix}.max_in_flight must be > 0 for 'concurrency'")
    rate_limit["max_in_flight"] = max_in_flight


def _normalize_web_interface_policies(
    policies: dict[str, object],
    *,
    prefix: str,
) -> None:
    unknown_keys = sorted(key for key in policies if key not in _SUPPORTED_WEB_INTERFACE_POLICY_KEYS)
    if unknown_keys:
        raise ConfigError(f"{prefix} has unsupported keys: {unknown_keys}")

    if "request_size_bytes" in policies:
        request_size_bytes = policies.get("request_size_bytes")
        if not isinstance(request_size_bytes, int):
            raise ConfigError(f"{prefix}.request_size_bytes must be an integer when provided")
        if request_size_bytes <= 0:
            raise ConfigError(f"{prefix}.request_size_bytes must be > 0")
        policies["request_size_bytes"] = request_size_bytes

    if "timeout_ms" in policies:
        timeout_ms = policies.get("timeout_ms")
        if not isinstance(timeout_ms, int):
            raise ConfigError(f"{prefix}.timeout_ms must be an integer when provided")
        if timeout_ms <= 0:
            raise ConfigError(f"{prefix}.timeout_ms must be > 0")
        policies["timeout_ms"] = timeout_ms

    if "rate_limit" in policies:
        rate_limit = policies.get("rate_limit")
        if not isinstance(rate_limit, dict):
            raise ConfigError(f"{prefix}.rate_limit must be a mapping when provided")
        _normalize_rate_limit_policy(rate_limit, prefix=f"{prefix}.rate_limit")
        policies["rate_limit"] = rate_limit
