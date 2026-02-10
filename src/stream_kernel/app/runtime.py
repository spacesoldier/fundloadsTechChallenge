from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.cli import apply_cli_overrides, parse_args
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.execution import builder as execution_builder


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
