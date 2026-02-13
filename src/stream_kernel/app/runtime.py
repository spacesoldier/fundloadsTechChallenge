from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.cli import apply_cli_overrides, parse_args
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.execution.orchestration import builder as execution_builder


def runtime_contract_summary(config: dict[str, object]) -> dict[str, object]:
    # Build a normalized runtime contract snapshot used by preflight diagnostics.
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")

    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("runtime.platform must be a mapping")

    kv = platform.get("kv", {})
    if not isinstance(kv, dict):
        raise ValueError("runtime.platform.kv must be a mapping")
    kv_backend = kv.get("backend", "memory")
    if not isinstance(kv_backend, str) or not kv_backend:
        raise ValueError("runtime.platform.kv.backend must be a non-empty string")

    ordering = runtime.get("ordering", {})
    if not isinstance(ordering, dict):
        raise ValueError("runtime.ordering must be a mapping")
    ordering_sink_mode = ordering.get("sink_mode", "completion")
    if not isinstance(ordering_sink_mode, str) or not ordering_sink_mode:
        raise ValueError("runtime.ordering.sink_mode must be a non-empty string")

    execution_ipc_raw = platform.get("execution_ipc")
    execution_ipc_enabled = execution_ipc_raw is not None
    bootstrap_raw = platform.get("bootstrap", {})
    if not isinstance(bootstrap_raw, dict):
        raise ValueError("runtime.platform.bootstrap must be a mapping")
    bootstrap_mode = bootstrap_raw.get("mode", "inline")
    if not isinstance(bootstrap_mode, str) or not bootstrap_mode:
        raise ValueError("runtime.platform.bootstrap.mode must be a non-empty string")
    execution_ipc: dict[str, object]
    if execution_ipc_raw is None:
        execution_ipc = {
            "enabled": False,
            "transport": None,
            "bind_host": None,
            "bind_port": None,
            "auth_mode": None,
            "secret_mode": None,
            "kdf": None,
            "ttl_seconds": None,
            "max_payload_bytes": None,
        }
        execution_transport_profile = "memory"
    else:
        if not isinstance(execution_ipc_raw, dict):
            raise ValueError("runtime.platform.execution_ipc must be a mapping")
        auth = execution_ipc_raw.get("auth", {})
        if not isinstance(auth, dict):
            raise ValueError("runtime.platform.execution_ipc.auth must be a mapping")
        execution_ipc = {
            "enabled": execution_ipc_enabled,
            "transport": execution_ipc_raw.get("transport"),
            "bind_host": execution_ipc_raw.get("bind_host"),
            "bind_port": execution_ipc_raw.get("bind_port"),
            "auth_mode": auth.get("mode"),
            "secret_mode": auth.get("secret_mode"),
            "kdf": auth.get("kdf"),
            "ttl_seconds": auth.get("ttl_seconds"),
            "max_payload_bytes": execution_ipc_raw.get("max_payload_bytes"),
        }
        transport = execution_ipc_raw.get("transport")
        execution_transport_profile = transport if isinstance(transport, str) and transport else "memory"

    process_groups_raw = platform.get("process_groups", [])
    if not isinstance(process_groups_raw, list):
        raise ValueError("runtime.platform.process_groups must be a list")
    process_group_names: list[str] = []
    for index, group in enumerate(process_groups_raw):
        if not isinstance(group, dict):
            raise ValueError(f"runtime.platform.process_groups[{index}] must be a mapping")
        name = group.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"runtime.platform.process_groups[{index}].name must be a non-empty string")
        process_group_names.append(name)

    web_raw = runtime.get("web", {})
    if not isinstance(web_raw, dict):
        raise ValueError("runtime.web must be a mapping")
    interfaces_raw = web_raw.get("interfaces", [])
    if not isinstance(interfaces_raw, list):
        raise ValueError("runtime.web.interfaces must be a list")
    web_kinds: list[str] = []
    for index, interface in enumerate(interfaces_raw):
        if not isinstance(interface, dict):
            raise ValueError(f"runtime.web.interfaces[{index}] must be a mapping")
        kind = interface.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"runtime.web.interfaces[{index}].kind must be a non-empty string")
        web_kinds.append(kind)

    return {
        "kv_backend": kv_backend,
        "ordering_sink_mode": ordering_sink_mode,
        "bootstrap_mode": bootstrap_mode,
        "execution_transport_profile": execution_transport_profile,
        "execution_ipc": execution_ipc,
        "process_groups": {
            "count": len(process_group_names),
            "names": process_group_names,
        },
        "web": {
            "interface_count": len(web_kinds),
            "kinds": web_kinds,
        },
    }


def run_with_config(
    config: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None = None,
    adapter_bindings: dict[str, object] | None = None,
    discovery_modules: list[str] | None = None,
    argv_overrides: dict[str, str] | None = None,
    run_id: str = "run",
) -> int:
    # Build and run the pipeline from a validated newgen config.
    # Runtime contract snapshot is built first to fail fast on malformed runtime shape.
    _ = runtime_contract_summary(config)
    if argv_overrides:
        args = SimpleNamespace(
            input=argv_overrides.get("input"),
            output=argv_overrides.get("output"),
            tracing=argv_overrides.get("tracing"),
            trace_path=argv_overrides.get("trace_path"),
        )
        apply_cli_overrides(config, args, discovery_modules=discovery_modules)
    artifacts = execution_builder.build_runtime_artifacts(
        config,
        adapter_registry=adapter_registry,
        adapter_bindings=adapter_bindings,
        discovery_modules=discovery_modules,
        run_id=run_id,
    )
    execution_builder.execute_runtime_artifacts(artifacts)
    return 0


def run_with_registry(
    argv: list[str] | None,
    *,
    adapter_registry: AdapterRegistry,
    adapter_bindings: dict[str, object],
    discovery_modules: list[str],
) -> int:
    # Generic framework entrypoint: parse CLI, load/validate config, apply overrides, run pipeline.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args, discovery_modules=discovery_modules)
    return run_with_config(
        config,
        adapter_registry=adapter_registry,
        adapter_bindings=adapter_bindings,
        discovery_modules=discovery_modules,
        argv_overrides=None,
    )


def run(argv: list[str] | None) -> int:
    # Generic framework entrypoint using discovered adapter kinds from config.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args, discovery_modules=None)
    return run_with_config(config, argv_overrides=None, run_id="run")
