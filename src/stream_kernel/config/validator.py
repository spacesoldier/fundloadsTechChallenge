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
_SUPPORTED_BOUNDARY_DISPATCH_MODES = {"stream", "batch"}
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
_SUPPORTED_OBSERVABILITY_TRACE_EXPORTER_KINDS = {
    "jsonl",
    "stdout",
    "otel_otlp",
    "otel_otlp_logical",
    "otel_otlp_topology",
    "opentracing_bridge",
}
_SUPPORTED_OBSERVABILITY_OTEL_TRACE_VIEWS = {"logical", "topology"}
_SUPPORTED_OBSERVABILITY_TRACE_JSONL_SLICES = {"all", "business_logic", "platform_internals"}
_SUPPORTED_OBSERVABILITY_LOG_EXPORTER_KINDS = {"stdout", "stdout_plain", "jsonl", "file_plain", "otel_logs_otlp"}
_SUPPORTED_OBSERVABILITY_LOG_EXPORTER_MODES = {"lifecycle", "all"}
_SUPPORTED_OBSERVABILITY_LOG_LEVELS = {"off", "none", "info", "debug", "full"}
_SUPPORTED_OBSERVABILITY_MONITORING_EXPORTER_KINDS = {"prometheus"}
_SUPPORTED_OBSERVABILITY_PROMETHEUS_MODES = {"http_pull", "textfile"}
_SUPPORTED_OBSERVABILITY_OTEL_BACKENDS = {"urllib", "requests", "httpx", "aiohttp", "urllib3", "grpcio", "otel_sdk"}
_OBSERVABILITY_ASYNC_ONLY_BACKENDS = {"aiohttp"}
_OBSERVABILITY_SYNC_ONLY_BACKENDS = {"urllib", "requests", "urllib3", "grpcio", "otel_sdk"}
_SUPPORTED_OBSERVABILITY_QUEUE_DROP_POLICIES = {"drop_newest", "drop_oldest", "block_with_timeout"}
_SUPPORTED_OBSERVABILITY_PIPELINE_MODES = {"tracing_only", "full_multi_stream"}
_SUPPORTED_OBSERVABILITY_PIPELINE_STREAMS = {"tracing", "logging", "telemetry", "monitoring"}
_SUPPORTED_OBSERVABILITY_PIPELINE_SYSTEM_NODE_KINDS = {
    "system.obs.trace_dispatch",
    "system.obs.log_dispatch",
    "system.obs.metric_dispatch",
    "system.obs.monitor_dispatch",
}
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


def _flatten_observability_otel_exporter_grouped_settings(
    settings: dict[str, object],
    *,
    prefix: str,
) -> None:
    # Support grouped settings while keeping flat keys backward compatible.
    otlp = settings.get("otlp")
    if otlp is not None and not isinstance(otlp, dict):
        raise ConfigError(f"{prefix}.otlp must be a mapping when provided")
    transport = settings.get("transport")
    if transport is not None and not isinstance(transport, dict):
        raise ConfigError(f"{prefix}.transport must be a mapping when provided")
    service = settings.get("service")
    if service is not None and not isinstance(service, dict):
        raise ConfigError(f"{prefix}.service must be a mapping when provided")
    view = settings.get("view")
    if view is not None and not isinstance(view, dict):
        raise ConfigError(f"{prefix}.view must be a mapping when provided")

    if isinstance(otlp, dict):
        if "endpoint" in otlp:
            settings["endpoint"] = otlp["endpoint"]
        if "headers" in otlp:
            settings["headers"] = otlp["headers"]

    if isinstance(transport, dict):
        for key in (
            "backend",
            "timeout_seconds",
            "dependency_missing",
            "bridge",
            "batch",
            "queue",
            "retry",
            "httpx",
            "grpc",
            "urllib3",
            "aiohttp",
        ):
            if key in transport:
                settings[key] = transport[key]

    if isinstance(service, dict):
        for key in (
            "service_name",
            "service_namespace",
            "service_version",
            "service_instance_id",
            "deployment_environment",
        ):
            if key in service:
                settings[key] = service[key]

    if isinstance(view, dict):
        for key in (
            "trace_view",
            "service_name_by_step",
            "service_name_by_process_group",
            "service_name_suffix",
            "isolate_view_ids",
            "include_runtime_resource",
            "span_kind",
        ):
            if key in view:
                settings[key] = view[key]


def _resolve_observability_otel_exporter_backend(
    exporter: dict[str, object],
    settings: dict[str, object],
    *,
    prefix: str,
) -> str:
    backend: object | None = exporter.get("backend")
    if backend is None:
        transport = settings.get("transport")
        if isinstance(transport, dict):
            backend = transport.get("backend")
    if backend is None:
        backend = settings.get("backend")
    if backend is None:
        backend = "urllib"
    if not isinstance(backend, str) or not backend:
        raise ConfigError(f"{prefix}.backend must be a non-empty string")
    if backend not in _SUPPORTED_OBSERVABILITY_OTEL_BACKENDS:
        raise ConfigError(
            f"{prefix}.backend must be one of: {sorted(_SUPPORTED_OBSERVABILITY_OTEL_BACKENDS)}"
        )
    return backend


def _default_bootstrap_mode(platform: dict[str, object]) -> str:
    process_groups = platform.get("process_groups")
    if isinstance(process_groups, list) and len(process_groups) > 0:
        return "process_supervisor"
    return "inline"


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
    bootstrap_mode = bootstrap.get("mode", _default_bootstrap_mode(platform))
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

            if "runner_profile" in group:
                runner_profile = group.get("runner_profile")
                if not isinstance(runner_profile, str) or not runner_profile:
                    raise ConfigError(
                        "runtime.platform.process_groups["
                        f"{index}].runner_profile must be a non-empty string when provided"
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

    boundary_dispatch = platform.get("boundary_dispatch")
    if boundary_dispatch is not None:
        if not isinstance(boundary_dispatch, dict):
            raise ConfigError("runtime.platform.boundary_dispatch must be a mapping when provided")
        unknown_keys = [
            key
            for key in boundary_dispatch
            if key not in {"mode", "batch_max_items", "stream_batch_max_items", "control_poll_ms", "timeout_seconds"}
        ]
        if unknown_keys:
            raise ConfigError(
                "runtime.platform.boundary_dispatch has unsupported keys: "
                f"{sorted(unknown_keys)}"
            )
        mode = boundary_dispatch.get("mode", "stream")
        if not isinstance(mode, str) or not mode:
            raise ConfigError("runtime.platform.boundary_dispatch.mode must be a non-empty string when provided")
        if mode not in _SUPPORTED_BOUNDARY_DISPATCH_MODES:
            raise ConfigError(
                "runtime.platform.boundary_dispatch.mode must be one of: "
                f"{sorted(_SUPPORTED_BOUNDARY_DISPATCH_MODES)}"
            )
        boundary_dispatch["mode"] = mode
        batch_max_items = boundary_dispatch.get("batch_max_items", 1000)
        if not isinstance(batch_max_items, int):
            raise ConfigError("runtime.platform.boundary_dispatch.batch_max_items must be an integer when provided")
        if batch_max_items <= 0:
            raise ConfigError("runtime.platform.boundary_dispatch.batch_max_items must be > 0")
        boundary_dispatch["batch_max_items"] = batch_max_items
        stream_batch_max_items = boundary_dispatch.get("stream_batch_max_items", 1)
        if not isinstance(stream_batch_max_items, int):
            raise ConfigError(
                "runtime.platform.boundary_dispatch.stream_batch_max_items must be an integer when provided"
            )
        if stream_batch_max_items <= 0:
            raise ConfigError("runtime.platform.boundary_dispatch.stream_batch_max_items must be > 0")
        boundary_dispatch["stream_batch_max_items"] = stream_batch_max_items
        control_poll_ms = boundary_dispatch.get("control_poll_ms", 1.0)
        if not isinstance(control_poll_ms, (int, float)):
            raise ConfigError("runtime.platform.boundary_dispatch.control_poll_ms must be a number when provided")
        if control_poll_ms <= 0:
            raise ConfigError("runtime.platform.boundary_dispatch.control_poll_ms must be > 0")
        boundary_dispatch["control_poll_ms"] = float(control_poll_ms)
        timeout_seconds = boundary_dispatch.get("timeout_seconds", 10.0)
        if not isinstance(timeout_seconds, (int, float)):
            raise ConfigError("runtime.platform.boundary_dispatch.timeout_seconds must be a number when provided")
        if timeout_seconds <= 0:
            raise ConfigError("runtime.platform.boundary_dispatch.timeout_seconds must be > 0")
        boundary_dispatch["timeout_seconds"] = float(timeout_seconds)


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
    _normalize_observability_pipeline(observability)

    tracing = observability.get("tracing", {})
    if not isinstance(tracing, dict):
        raise ConfigError("runtime.observability.tracing must be a mapping when provided")
    observability["tracing"] = tracing
    dispatch_queue = tracing.get("dispatch_queue", {})
    if not isinstance(dispatch_queue, dict):
        raise ConfigError("runtime.observability.tracing.dispatch_queue must be a mapping when provided")
    max_items = dispatch_queue.get("max_items", 8192)
    if not isinstance(max_items, int) or max_items <= 0:
        raise ConfigError("runtime.observability.tracing.dispatch_queue.max_items must be an integer > 0")
    dispatch_queue["max_items"] = max_items
    drop_policy = dispatch_queue.get("drop_policy", "drop_newest")
    if not isinstance(drop_policy, str) or not drop_policy:
        raise ConfigError("runtime.observability.tracing.dispatch_queue.drop_policy must be a non-empty string")
    if drop_policy not in _SUPPORTED_OBSERVABILITY_QUEUE_DROP_POLICIES:
        raise ConfigError(
            "runtime.observability.tracing.dispatch_queue.drop_policy must be one of: "
            f"{sorted(_SUPPORTED_OBSERVABILITY_QUEUE_DROP_POLICIES)}"
        )
    dispatch_queue["drop_policy"] = drop_policy
    if drop_policy == "block_with_timeout":
        block_timeout_ms = dispatch_queue.get("block_timeout_ms", 100)
        if not isinstance(block_timeout_ms, int) or block_timeout_ms <= 0:
            raise ConfigError("runtime.observability.tracing.dispatch_queue.block_timeout_ms must be an integer > 0")
        dispatch_queue["block_timeout_ms"] = block_timeout_ms
    elif "block_timeout_ms" in dispatch_queue:
        block_timeout_ms = dispatch_queue.get("block_timeout_ms")
        if not isinstance(block_timeout_ms, int) or block_timeout_ms <= 0:
            raise ConfigError("runtime.observability.tracing.dispatch_queue.block_timeout_ms must be an integer > 0")
    tracing["dispatch_queue"] = dispatch_queue

    tracing_exporters = tracing.get("exporters", [])
    if not isinstance(tracing_exporters, list):
        raise ConfigError("runtime.observability.tracing.exporters must be a list when provided")
    for index, exporter in enumerate(tracing_exporters):
        if not isinstance(exporter, dict):
            raise ConfigError(f"runtime.observability.tracing.exporters[{index}] must be a mapping")
        enabled = exporter.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                f"runtime.observability.tracing.exporters[{index}].enabled must be a boolean when provided"
            )
        exporter["enabled"] = enabled
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
        if kind in {"otel_otlp", "otel_otlp_logical", "otel_otlp_topology"}:
            _flatten_observability_otel_exporter_grouped_settings(
                settings,
                prefix=f"runtime.observability.tracing.exporters[{index}].settings",
            )
            backend = _resolve_observability_otel_exporter_backend(
                exporter,
                settings,
                prefix=f"runtime.observability.tracing.exporters[{index}]",
            )
            exporter["backend"] = backend
            _normalize_observability_otel_exporter_settings(
                settings,
                prefix=f"runtime.observability.tracing.exporters[{index}].settings",
            )
        elif kind == "jsonl":
            path = settings.get("path")
            if not isinstance(path, str) or not path:
                raise ConfigError(
                    f"runtime.observability.tracing.exporters[{index}].settings.path "
                    "must be a non-empty string for kind 'jsonl'"
                )
            view = settings.get("view")
            if view is not None and not isinstance(view, dict):
                raise ConfigError(
                    f"runtime.observability.tracing.exporters[{index}].settings.view "
                    "must be a mapping when provided"
                )
            trace_slice = settings.get("trace_slice")
            if trace_slice is not None:
                if not isinstance(trace_slice, str) or not trace_slice:
                    raise ConfigError(
                        f"runtime.observability.tracing.exporters[{index}].settings.trace_slice "
                        "must be a non-empty string when provided"
                    )
                normalized = _normalize_trace_jsonl_slice(trace_slice)
                if normalized is None:
                    raise ConfigError(
                        "runtime.observability.tracing.exporters["
                        f"{index}].settings.trace_slice must be one of: "
                        f"{sorted(_SUPPORTED_OBSERVABILITY_TRACE_JSONL_SLICES)} "
                        "(aliases: logical|topology|full)"
                    )
                settings["trace_slice"] = normalized
            if isinstance(view, dict) and "trace_view" in view:
                trace_view = view.get("trace_view")
                if not isinstance(trace_view, str) or not trace_view:
                    raise ConfigError(
                        f"runtime.observability.tracing.exporters[{index}].settings.view.trace_view "
                        "must be a non-empty string when provided"
                    )
                normalized = _normalize_trace_jsonl_slice(trace_view)
                if normalized is None:
                    raise ConfigError(
                        "runtime.observability.tracing.exporters["
                        f"{index}].settings.view.trace_view must be one of: "
                        f"{sorted(_SUPPORTED_OBSERVABILITY_TRACE_JSONL_SLICES)} "
                        "(aliases: logical|topology|full)"
                    )
                settings["trace_slice"] = normalized
        elif "backend" in exporter:
            raise ConfigError(
                "runtime.observability.tracing.exporters["
                f"{index}].backend is supported only for kind 'otel_otlp*'"
            )
        exporter["settings"] = settings

    _validate_observability_exporter_runner_compatibility(runtime=runtime, exporters=tracing_exporters)

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
        mode = exporter.get("mode", "lifecycle")
        if not isinstance(mode, str) or not mode:
            raise ConfigError(
                f"runtime.observability.logging.exporters[{index}].mode must be a non-empty string when provided"
            )
        mode = mode.lower()
        if mode not in _SUPPORTED_OBSERVABILITY_LOG_EXPORTER_MODES:
            raise ConfigError(
                "runtime.observability.logging.exporters["
                f"{index}].mode must be one of: {sorted(_SUPPORTED_OBSERVABILITY_LOG_EXPORTER_MODES)}"
            )
        exporter["mode"] = mode
        settings = exporter.get("settings", {})
        if not isinstance(settings, dict):
            raise ConfigError(
                f"runtime.observability.logging.exporters[{index}].settings must be a mapping when provided"
            )
        if kind in {"jsonl", "file_plain"}:
            path = settings.get("path")
            if path is not None and (not isinstance(path, str) or not path):
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.path "
                    f"must be a non-empty string when provided for kind '{kind}'"
                )
            file_prefix = settings.get("file_prefix")
            if file_prefix is not None and (not isinstance(file_prefix, str) or not file_prefix):
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.file_prefix "
                    "must be a non-empty string when provided"
                )
            flush_every_n = settings.get("flush_every_n")
            if flush_every_n is not None and (not isinstance(flush_every_n, int) or flush_every_n <= 0):
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.flush_every_n "
                    "must be an integer > 0 when provided"
                )
            fsync_every_n = settings.get("fsync_every_n")
            if fsync_every_n is not None and (not isinstance(fsync_every_n, int) or fsync_every_n <= 0):
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.fsync_every_n "
                    "must be an integer > 0 when provided"
                )
            if "workers_dir" in settings:
                raise ConfigError(
                    f"runtime.observability.logging.exporters[{index}].settings.workers_dir "
                    "is not supported; worker-local lifecycle log sinks are disabled"
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
    level = level.lower()
    if level not in _SUPPORTED_OBSERVABILITY_LOG_LEVELS:
        raise ConfigError(
            "runtime.observability.logging.lifecycle_events.level must be one of: "
            f"{sorted(_SUPPORTED_OBSERVABILITY_LOG_LEVELS)}"
        )
    lifecycle_events["level"] = level

    monitoring = observability.get("monitoring", {})
    if not isinstance(monitoring, dict):
        raise ConfigError("runtime.observability.monitoring must be a mapping when provided")
    observability["monitoring"] = monitoring
    monitoring_exporters = monitoring.get("exporters", [])
    if not isinstance(monitoring_exporters, list):
        raise ConfigError("runtime.observability.monitoring.exporters must be a list when provided")
    for index, exporter in enumerate(monitoring_exporters):
        if not isinstance(exporter, dict):
            raise ConfigError(f"runtime.observability.monitoring.exporters[{index}] must be a mapping")
        kind = exporter.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ConfigError(f"runtime.observability.monitoring.exporters[{index}].kind must be a non-empty string")
        if kind not in _SUPPORTED_OBSERVABILITY_MONITORING_EXPORTER_KINDS:
            raise ConfigError(
                "runtime.observability.monitoring.exporters["
                f"{index}].kind must be one of: {sorted(_SUPPORTED_OBSERVABILITY_MONITORING_EXPORTER_KINDS)}"
            )
        settings = exporter.get("settings", {})
        if not isinstance(settings, dict):
            raise ConfigError(
                f"runtime.observability.monitoring.exporters[{index}].settings must be a mapping when provided"
            )
        if kind == "prometheus":
            mode = settings.get("mode", "http_pull")
            if not isinstance(mode, str) or not mode:
                raise ConfigError(
                    f"runtime.observability.monitoring.exporters[{index}].settings.mode "
                    "must be a non-empty string when provided"
                )
            mode = mode.lower()
            if mode not in _SUPPORTED_OBSERVABILITY_PROMETHEUS_MODES:
                raise ConfigError(
                    "runtime.observability.monitoring.exporters["
                    f"{index}].settings.mode must be one of: {sorted(_SUPPORTED_OBSERVABILITY_PROMETHEUS_MODES)}"
                )
            settings["mode"] = mode

            for field in ("namespace", "subsystem"):
                value = settings.get(field)
                if value is not None and (not isinstance(value, str) or not value):
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.{field} must be a non-empty string when provided"
                    )

            include_labels = settings.get("include_labels")
            if include_labels is not None:
                if not isinstance(include_labels, dict):
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.include_labels must be a mapping when provided"
                    )
                for key, value in include_labels.items():
                    if not isinstance(key, str) or not isinstance(value, bool):
                        raise ConfigError(
                            "runtime.observability.monitoring.exporters["
                            f"{index}].settings.include_labels must be a string-to-boolean mapping"
                        )

            if mode == "http_pull":
                http = settings.get("http", {})
                if not isinstance(http, dict):
                    raise ConfigError(
                        f"runtime.observability.monitoring.exporters[{index}].settings.http must be a mapping"
                    )
                host = http.get("host", "127.0.0.1")
                if not isinstance(host, str) or not host:
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.http.host must be a non-empty string"
                    )
                port = http.get("port", 9464)
                if not isinstance(port, int) or port <= 0:
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.http.port must be an integer > 0"
                    )
                path = http.get("path", "/metrics")
                if not isinstance(path, str) or not path:
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.http.path must be a non-empty string"
                    )
                http["host"] = host
                http["port"] = port
                http["path"] = path
                settings["http"] = http
            else:
                textfile = settings.get("textfile", {})
                if not isinstance(textfile, dict):
                    raise ConfigError(
                        f"runtime.observability.monitoring.exporters[{index}].settings.textfile must be a mapping"
                    )
                path = textfile.get("path", "metrics/stream_kernel.prom")
                if not isinstance(path, str) or not path:
                    raise ConfigError(
                        "runtime.observability.monitoring.exporters["
                        f"{index}].settings.textfile.path must be a non-empty string"
                    )
                textfile["path"] = path
                settings["textfile"] = textfile
        exporter["settings"] = settings

    pipeline = observability.get("pipeline")
    if pipeline is not None and (tracing_exporters or log_exporters or monitoring_exporters):
        raise ConfigError(
            "runtime.observability.pipeline cannot be combined with "
            "runtime.observability.tracing.exporters or runtime.observability.logging.exporters "
            "or runtime.observability.monitoring.exporters"
        )


def _normalize_observability_pipeline(observability: dict[str, object]) -> None:
    pipeline = observability.get("pipeline")
    if pipeline is None:
        return
    if not isinstance(pipeline, dict):
        raise ConfigError("runtime.observability.pipeline must be a mapping when provided")
    observability["pipeline"] = pipeline

    unknown_keys = sorted(
        key for key in pipeline if key not in {"mode", "streams", "system_nodes", "strict_bindings"}
    )
    if unknown_keys:
        raise ConfigError(
            "runtime.observability.pipeline has unsupported keys: "
            f"{unknown_keys}"
        )

    mode = pipeline.get("mode", "tracing_only")
    if not isinstance(mode, str) or not mode:
        raise ConfigError("runtime.observability.pipeline.mode must be a non-empty string when provided")
    if mode not in _SUPPORTED_OBSERVABILITY_PIPELINE_MODES:
        raise ConfigError(
            "runtime.observability.pipeline.mode must be one of: "
            f"{sorted(_SUPPORTED_OBSERVABILITY_PIPELINE_MODES)}"
        )
    pipeline["mode"] = mode

    streams = pipeline.get("streams")
    if streams is None:
        if mode == "tracing_only":
            streams = ["tracing"]
        else:
            streams = ["tracing", "logging", "telemetry", "monitoring"]
    if not isinstance(streams, list):
        raise ConfigError("runtime.observability.pipeline.streams must be a list when provided")
    if not all(isinstance(item, str) and item for item in streams):
        raise ConfigError("runtime.observability.pipeline.streams entries must be non-empty strings")
    unknown_streams = sorted(item for item in streams if item not in _SUPPORTED_OBSERVABILITY_PIPELINE_STREAMS)
    if unknown_streams:
        raise ConfigError(
            "runtime.observability.pipeline.streams entries must be one of: "
            f"{sorted(_SUPPORTED_OBSERVABILITY_PIPELINE_STREAMS)}"
        )
    streams_set = set(streams)
    if mode == "tracing_only" and streams_set != {"tracing"}:
        raise ConfigError(
            "runtime.observability.pipeline.mode=tracing_only requires streams=['tracing']"
        )
    if mode == "full_multi_stream" and streams_set != _SUPPORTED_OBSERVABILITY_PIPELINE_STREAMS:
        raise ConfigError(
            "runtime.observability.pipeline.mode=full_multi_stream requires streams="
            "['tracing', 'logging', 'telemetry', 'monitoring']"
        )
    pipeline["streams"] = list(streams)

    strict_bindings = pipeline.get("strict_bindings", True)
    if not isinstance(strict_bindings, bool):
        raise ConfigError("runtime.observability.pipeline.strict_bindings must be a boolean when provided")
    pipeline["strict_bindings"] = strict_bindings

    system_nodes = pipeline.get("system_nodes", [])
    if not isinstance(system_nodes, list):
        raise ConfigError("runtime.observability.pipeline.system_nodes must be a list when provided")
    normalized_nodes: list[dict[str, object]] = []
    for index, node in enumerate(system_nodes):
        if not isinstance(node, dict):
            raise ConfigError(
                f"runtime.observability.pipeline.system_nodes[{index}] must be a mapping"
            )
        unknown_node_keys = sorted(key for key in node if key not in {"kind", "enabled", "qualifier"})
        if unknown_node_keys:
            raise ConfigError(
                "runtime.observability.pipeline.system_nodes["
                f"{index}] has unsupported keys: {unknown_node_keys}"
            )
        kind = node.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ConfigError(
                f"runtime.observability.pipeline.system_nodes[{index}].kind must be a non-empty string"
            )
        if kind not in _SUPPORTED_OBSERVABILITY_PIPELINE_SYSTEM_NODE_KINDS:
            raise ConfigError(
                "runtime.observability.pipeline.system_nodes["
                f"{index}].kind must be one of: {sorted(_SUPPORTED_OBSERVABILITY_PIPELINE_SYSTEM_NODE_KINDS)}"
            )
        enabled = node.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                f"runtime.observability.pipeline.system_nodes[{index}].enabled must be a boolean when provided"
            )
        qualifier = node.get("qualifier")
        if qualifier is not None and (not isinstance(qualifier, str) or not qualifier):
            raise ConfigError(
                f"runtime.observability.pipeline.system_nodes[{index}].qualifier "
                "must be a non-empty string when provided"
            )
        normalized: dict[str, object] = {"kind": kind, "enabled": enabled}
        if isinstance(qualifier, str):
            normalized["qualifier"] = qualifier
        normalized_nodes.append(normalized)
    pipeline["system_nodes"] = normalized_nodes


def _normalize_observability_otel_exporter_settings(
    settings: dict[str, object],
    *,
    prefix: str,
) -> None:
    endpoint = settings.get("endpoint")
    if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
        raise ConfigError(f"{prefix}.endpoint must be a non-empty string when provided")

    headers = settings.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            raise ConfigError(f"{prefix}.headers must be a mapping when provided")
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ConfigError(f"{prefix}.headers must be a string-to-string mapping")

    timeout_seconds = settings.get("timeout_seconds")
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, (int, float)):
            raise ConfigError(f"{prefix}.timeout_seconds must be numeric when provided")
        if float(timeout_seconds) <= 0:
            raise ConfigError(f"{prefix}.timeout_seconds must be > 0 when provided")
        settings["timeout_seconds"] = float(timeout_seconds)

    bridge = settings.get("bridge", False)
    if not isinstance(bridge, bool):
        raise ConfigError(f"{prefix}.bridge must be a boolean when provided")
    settings["bridge"] = bridge

    dependency_missing = settings.get("dependency_missing", "error")
    if not isinstance(dependency_missing, str) or dependency_missing not in {"error", "degrade_noop"}:
        raise ConfigError(
            f"{prefix}.dependency_missing must be one of: ['error', 'degrade_noop']"
        )
    settings["dependency_missing"] = dependency_missing

    trace_view = settings.get("trace_view")
    if trace_view is not None:
        if not isinstance(trace_view, str) or trace_view not in _SUPPORTED_OBSERVABILITY_OTEL_TRACE_VIEWS:
            raise ConfigError(
                f"{prefix}.trace_view must be one of: {sorted(_SUPPORTED_OBSERVABILITY_OTEL_TRACE_VIEWS)}"
            )
        settings["trace_view"] = trace_view

    service_name_suffix = settings.get("service_name_suffix")
    if service_name_suffix is not None:
        if not isinstance(service_name_suffix, str) or not service_name_suffix:
            raise ConfigError(f"{prefix}.service_name_suffix must be a non-empty string when provided")
        settings["service_name_suffix"] = service_name_suffix

    service_name_by_step = settings.get("service_name_by_step")
    if service_name_by_step is not None:
        if not isinstance(service_name_by_step, bool):
            raise ConfigError(f"{prefix}.service_name_by_step must be a boolean when provided")
        settings["service_name_by_step"] = service_name_by_step

    isolate_view_ids = settings.get("isolate_view_ids")
    if isolate_view_ids is not None:
        if not isinstance(isolate_view_ids, bool):
            raise ConfigError(f"{prefix}.isolate_view_ids must be a boolean when provided")
        settings["isolate_view_ids"] = isolate_view_ids

    batch = settings.get("batch", {})
    if not isinstance(batch, dict):
        raise ConfigError(f"{prefix}.batch must be a mapping when provided")
    max_items = batch.get("max_items", 100)
    if not isinstance(max_items, int) or max_items <= 0:
        raise ConfigError(f"{prefix}.batch.max_items must be an integer > 0 when provided")
    batch["max_items"] = max_items
    flush_interval_ms = batch.get("flush_interval_ms", 1000)
    if not isinstance(flush_interval_ms, int) or flush_interval_ms < 0:
        raise ConfigError(f"{prefix}.batch.flush_interval_ms must be an integer >= 0 when provided")
    batch["flush_interval_ms"] = flush_interval_ms
    settings["batch"] = batch

    queue = settings.get("queue", {})
    if not isinstance(queue, dict):
        raise ConfigError(f"{prefix}.queue must be a mapping when provided")
    queue_max_items = queue.get("max_items", 10000)
    if not isinstance(queue_max_items, int) or queue_max_items <= 0:
        raise ConfigError(f"{prefix}.queue.max_items must be an integer > 0 when provided")
    queue["max_items"] = queue_max_items
    drop_policy = queue.get("drop_policy", "drop_newest")
    if not isinstance(drop_policy, str) or not drop_policy:
        raise ConfigError(f"{prefix}.queue.drop_policy must be a non-empty string when provided")
    if drop_policy not in _SUPPORTED_OBSERVABILITY_QUEUE_DROP_POLICIES:
        raise ConfigError(
            f"{prefix}.queue.drop_policy must be one of: {sorted(_SUPPORTED_OBSERVABILITY_QUEUE_DROP_POLICIES)}"
        )
    queue["drop_policy"] = drop_policy
    block_timeout_ms = queue.get("block_timeout_ms", 100)
    if not isinstance(block_timeout_ms, int) or block_timeout_ms <= 0:
        raise ConfigError(f"{prefix}.queue.block_timeout_ms must be an integer > 0 when provided")
    queue["block_timeout_ms"] = block_timeout_ms
    settings["queue"] = queue

    retry = settings.get("retry", {})
    if not isinstance(retry, dict):
        raise ConfigError(f"{prefix}.retry must be a mapping when provided")
    max_attempts = retry.get("max_attempts", 0)
    if not isinstance(max_attempts, int) or max_attempts < 0:
        raise ConfigError(f"{prefix}.retry.max_attempts must be an integer >= 0 when provided")
    retry["max_attempts"] = max_attempts
    backoff_ms = retry.get("backoff_ms", 0)
    if not isinstance(backoff_ms, int) or backoff_ms < 0:
        raise ConfigError(f"{prefix}.retry.backoff_ms must be an integer >= 0 when provided")
    retry["backoff_ms"] = backoff_ms
    settings["retry"] = retry

    httpx = settings.get("httpx", {})
    if not isinstance(httpx, dict):
        raise ConfigError(f"{prefix}.httpx must be a mapping when provided")
    mode = httpx.get("mode", "sync")
    if not isinstance(mode, str) or mode not in {"sync", "async"}:
        raise ConfigError(f"{prefix}.httpx.mode must be one of: ['sync', 'async']")
    httpx["mode"] = mode
    http2 = httpx.get("http2", False)
    if not isinstance(http2, bool):
        raise ConfigError(f"{prefix}.httpx.http2 must be a boolean when provided")
    httpx["http2"] = http2
    max_connections = httpx.get("max_connections")
    if max_connections is not None and (not isinstance(max_connections, int) or max_connections <= 0):
        raise ConfigError(f"{prefix}.httpx.max_connections must be an integer > 0 when provided")
    max_keepalive_connections = httpx.get("max_keepalive_connections")
    if max_keepalive_connections is not None and (
        not isinstance(max_keepalive_connections, int) or max_keepalive_connections <= 0
    ):
        raise ConfigError(
            f"{prefix}.httpx.max_keepalive_connections must be an integer > 0 when provided"
        )
    settings["httpx"] = httpx

    grpc = settings.get("grpc", {})
    if not isinstance(grpc, dict):
        raise ConfigError(f"{prefix}.grpc must be a mapping when provided")
    insecure = grpc.get("insecure", True)
    if not isinstance(insecure, bool):
        raise ConfigError(f"{prefix}.grpc.insecure must be a boolean when provided")
    grpc["insecure"] = insecure
    grpc_timeout_seconds = grpc.get("timeout_seconds")
    if grpc_timeout_seconds is not None:
        if not isinstance(grpc_timeout_seconds, (int, float)):
            raise ConfigError(f"{prefix}.grpc.timeout_seconds must be numeric when provided")
        if float(grpc_timeout_seconds) <= 0:
            raise ConfigError(f"{prefix}.grpc.timeout_seconds must be > 0 when provided")
        grpc["timeout_seconds"] = float(grpc_timeout_seconds)
    retryable_status_codes = grpc.get("retryable_status_codes", ["UNAVAILABLE", "DEADLINE_EXCEEDED"])
    if not isinstance(retryable_status_codes, list) or not all(
        isinstance(item, str) and item for item in retryable_status_codes
    ):
        raise ConfigError(
            f"{prefix}.grpc.retryable_status_codes must be a list of non-empty strings when provided"
        )
    grpc["retryable_status_codes"] = retryable_status_codes
    settings["grpc"] = grpc

    urllib3 = settings.get("urllib3", {})
    if not isinstance(urllib3, dict):
        raise ConfigError(f"{prefix}.urllib3 must be a mapping when provided")
    num_pools = urllib3.get("num_pools")
    if num_pools is not None and (not isinstance(num_pools, int) or num_pools <= 0):
        raise ConfigError(f"{prefix}.urllib3.num_pools must be an integer > 0 when provided")
    maxsize = urllib3.get("maxsize")
    if maxsize is not None and (not isinstance(maxsize, int) or maxsize <= 0):
        raise ConfigError(f"{prefix}.urllib3.maxsize must be an integer > 0 when provided")
    block = urllib3.get("block")
    if block is not None and not isinstance(block, bool):
        raise ConfigError(f"{prefix}.urllib3.block must be a boolean when provided")
    timeout_seconds = urllib3.get("timeout_seconds")
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, (int, float)):
            raise ConfigError(f"{prefix}.urllib3.timeout_seconds must be numeric when provided")
        if float(timeout_seconds) <= 0:
            raise ConfigError(f"{prefix}.urllib3.timeout_seconds must be > 0 when provided")
        urllib3["timeout_seconds"] = float(timeout_seconds)
    settings["urllib3"] = urllib3

    aiohttp = settings.get("aiohttp", {})
    if not isinstance(aiohttp, dict):
        raise ConfigError(f"{prefix}.aiohttp must be a mapping when provided")
    shutdown_timeout_seconds = aiohttp.get("shutdown_timeout_seconds", 2.0)
    if not isinstance(shutdown_timeout_seconds, (int, float)):
        raise ConfigError(f"{prefix}.aiohttp.shutdown_timeout_seconds must be numeric when provided")
    if float(shutdown_timeout_seconds) <= 0:
        raise ConfigError(f"{prefix}.aiohttp.shutdown_timeout_seconds must be > 0 when provided")
    aiohttp["shutdown_timeout_seconds"] = float(shutdown_timeout_seconds)
    connector_limit = aiohttp.get("connector_limit")
    if connector_limit is not None and (not isinstance(connector_limit, int) or connector_limit <= 0):
        raise ConfigError(f"{prefix}.aiohttp.connector_limit must be an integer > 0 when provided")
    connector_limit_per_host = aiohttp.get("connector_limit_per_host")
    if connector_limit_per_host is not None and (
        not isinstance(connector_limit_per_host, int) or connector_limit_per_host <= 0
    ):
        raise ConfigError(f"{prefix}.aiohttp.connector_limit_per_host must be an integer > 0 when provided")
    settings["aiohttp"] = aiohttp


def _validate_observability_exporter_runner_compatibility(
    *,
    runtime: dict[str, object],
    exporters: list[object],
) -> None:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list) or not groups:
        return
    runner_profiles: list[str] = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        if "runner_profile" not in group:
            continue
        runner_profile = group.get("runner_profile")
        if not isinstance(runner_profile, str) or runner_profile not in _SUPPORTED_PROCESS_GROUP_RUNNER_PROFILES:
            raise ConfigError(
                "runtime.platform.process_groups["
                f"{index}].runner_profile must be one of: {sorted(_SUPPORTED_PROCESS_GROUP_RUNNER_PROFILES)}"
            )
        runner_profiles.append(runner_profile)
    if not runner_profiles:
        return

    for index, exporter in enumerate(exporters):
        if not isinstance(exporter, dict):
            continue
        if exporter.get("enabled") is False:
            continue
        if exporter.get("kind") not in {"otel_otlp", "otel_otlp_logical", "otel_otlp_topology"}:
            continue
        backend = exporter.get("backend", "urllib")
        if not isinstance(backend, str):
            continue
        settings = exporter.get("settings", {})
        bridge = False
        if isinstance(settings, dict):
            bridge_raw = settings.get("bridge", False)
            bridge = bool(bridge_raw) if isinstance(bridge_raw, bool) else False
        for runner_profile in runner_profiles:
            if runner_profile == "sync" and backend in _OBSERVABILITY_ASYNC_ONLY_BACKENDS and not bridge:
                raise ConfigError(
                    "runtime.observability.tracing.exporters["
                    f"{index}] backend '{backend}' is async-only and requires settings.bridge=true "
                    "for runner_profile='sync'"
                )
            if runner_profile == "async" and backend in _OBSERVABILITY_SYNC_ONLY_BACKENDS and not bridge:
                raise ConfigError(
                    "runtime.observability.tracing.exporters["
                    f"{index}] backend '{backend}' is sync-only and requires settings.bridge=true "
                    "for runner_profile='async'"
                )


def _normalize_runtime_tracing(runtime: dict[str, object]) -> None:
    # Runtime tracing section is optional and validated as a mapping when provided.
    tracing = runtime.get("tracing")
    if tracing is None:
        return
    if not isinstance(tracing, dict):
        raise ConfigError("runtime.tracing must be a mapping when provided")
    runtime["tracing"] = tracing


def _normalize_trace_jsonl_slice(value: str) -> str | None:
    token = value.strip().lower()
    aliases = {
        "all": "all",
        "full": "all",
        "combined": "all",
        "logical": "business_logic",
        "business_logic": "business_logic",
        "topology": "platform_internals",
        "platform_internals": "platform_internals",
    }
    normalized = aliases.get(token)
    if normalized in _SUPPORTED_OBSERVABILITY_TRACE_JSONL_SLICES:
        return normalized
    return None


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
