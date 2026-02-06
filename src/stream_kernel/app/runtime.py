from __future__ import annotations

import importlib
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from fund_load.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from fund_load.ports.trace_sink import TraceSink
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.cli import apply_cli_overrides, parse_args
from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.kernel.context import ContextFactory
from stream_kernel.kernel.runner import Runner
from stream_kernel.kernel.trace import ErrorInfo, MessageSignature, TraceRecord, TraceRecorder
from stream_kernel.kernel.scenario import StepSpec


def run_with_config(
    config: dict[str, object],
    *,
    adapter_registry: AdapterRegistry,
    adapter_bindings: dict[str, object],
    discovery_modules: list[str],
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
        apply_cli_overrides(config, args)

    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")
    pipeline = runtime.get("pipeline")
    if not isinstance(pipeline, list) or not all(isinstance(name, str) for name in pipeline):
        raise ValueError("runtime.pipeline must be a list of step names")

    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")

    adapter_instances = _build_adapter_instances_from_registry(adapters, adapter_registry)
    # Build injection registry from provided bindings using shared adapter instances.
    injection_registry = _build_injection_registry_from_bindings(adapter_instances, adapter_bindings)

    ctx = ApplicationContext()
    modules = [importlib.import_module(name) for name in discovery_modules]
    ctx.discover(modules)
    consumer_registry = ctx.build_consumer_registry()

    scenario_name = _scenario_name(config)
    scenario = ctx.build_scenario(
        scenario_id=scenario_name,
        step_names=pipeline,
        wiring={
            "injection_registry": injection_registry,
            "consumer_registry": consumer_registry,
            "config": config,
            "strict": bool(runtime.get("strict", True)),
        },
    )

    # Input is provided by adapter role "input_source".
    input_source = adapter_instances.get("input_source")
    if input_source is None:
        raise ValueError("adapters.input_source must be configured")
    inputs = input_source.read()

    trace_recorder, trace_sink = _build_tracing(runtime)
    _emit_implicit_sink_diagnostics(
        config,
        scenario.steps,
        adapter_instances=adapter_instances,
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
        run_id=run_id,
        scenario_id=scenario_name,
    )
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory(run_id, scenario_name),
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
    )
    runner.run(inputs, output_sink=lambda _: None)
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
    apply_cli_overrides(config, args)
    return run_with_config(
        config,
        adapter_registry=adapter_registry,
        adapter_bindings=adapter_bindings,
        discovery_modules=discovery_modules,
        argv_overrides=None,
    )


def run(argv: list[str] | None) -> int:
    # Generic framework entrypoint using adapter factories declared in config.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args)

    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")
    discovery = runtime.get("discovery_modules", [])
    if not isinstance(discovery, list) or not all(isinstance(item, str) for item in discovery):
        raise ValueError("runtime.discovery_modules must be a list of strings")

    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")

    instances = _build_adapter_instances(adapters)
    injection_registry = _build_injection_registry(adapters, instances)

    ctx = ApplicationContext()
    modules = [importlib.import_module(name) for name in discovery]
    ctx.discover(modules)
    consumer_registry = ctx.build_consumer_registry()

    scenario_name = _scenario_name(config)
    pipeline = runtime.get("pipeline")
    if not isinstance(pipeline, list) or not all(isinstance(name, str) for name in pipeline):
        raise ValueError("runtime.pipeline must be a list of step names")

    scenario = ctx.build_scenario(
        scenario_id=scenario_name,
        step_names=pipeline,
        wiring={
            "injection_registry": injection_registry,
            "consumer_registry": consumer_registry,
            "config": config,
            "strict": bool(runtime.get("strict", True)),
        },
    )

    input_source = instances.get("input_source")
    if input_source is None:
        raise ValueError("adapters.input_source must be configured")

    trace_recorder, trace_sink = _build_tracing(runtime)
    _emit_implicit_sink_diagnostics(
        config,
        scenario.steps,
        adapter_instances=instances,
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
        run_id="run",
        scenario_id=scenario_name,
    )
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", scenario_name),
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
    )
    runner.run(input_source.read(), output_sink=lambda _: None)
    return 0


def _resolve_symbol(path: str) -> object:
    # Resolve "module:attr" or "module.attr" to a Python object.
    if ":" in path:
        module_name, attr = path.split(":", 1)
    else:
        module_name, _, attr = path.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"Invalid symbol path: {path}")
    module: ModuleType = importlib.import_module(module_name)
    return getattr(module, attr)


def _build_adapter_instances(adapters: dict[str, object]) -> dict[str, object]:
    instances: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        factory_path = cfg.get("factory")
        if not isinstance(factory_path, str):
            raise ValueError(f"adapters.{role}.factory must be a string")
        settings = cfg.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError(f"adapters.{role}.settings must be a mapping")
        factory = _resolve_symbol(factory_path)
        if not callable(factory):
            raise ValueError(f"adapters.{role}.factory must be callable")
        instances[role] = factory(settings)
    return instances


def _build_adapter_instances_from_registry(
    adapters: dict[str, object],
    registry: AdapterRegistry,
) -> dict[str, object]:
    # Build adapter instances using AdapterRegistry (role/kind), used for diagnostics.
    instances: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        instances[role] = registry.build(role, cfg)
    return instances


def _build_injection_registry_from_bindings(
    instances: dict[str, object],
    bindings: dict[str, object],
) -> InjectionRegistry:
    # Build InjectionRegistry from explicit bindings using shared instances (runtime wiring).
    injection = InjectionRegistry()
    for role, binding in bindings.items():
        if role not in instances:
            raise ValueError(f"Missing adapter instance for role: {role}")
        adapter = instances[role]
        if isinstance(binding, list):
            for port_type, data_type in binding:
                injection.register_factory(port_type, data_type, lambda _a=adapter: _a)
        else:
            port_type, data_type = binding
            injection.register_factory(port_type, data_type, lambda _a=adapter: _a)
    return injection


def _build_injection_registry(
    adapters: dict[str, object],
    instances: dict[str, object],
) -> InjectionRegistry:
    injection = InjectionRegistry()
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        binds = cfg.get("binds", [])
        if not isinstance(binds, list):
            raise ValueError(f"adapters.{role}.binds must be a list")
        adapter = instances.get(role)
        for bind in binds:
            if not isinstance(bind, dict):
                raise ValueError(f"adapters.{role}.binds entries must be mappings")
            port_type = bind.get("port_type")
            type_path = bind.get("type")
            if not isinstance(port_type, str) or not isinstance(type_path, str):
                raise ValueError(f"adapters.{role}.binds entries must define port_type and type")
            port_cls = _resolve_symbol(type_path)
            injection.register_factory(port_type, port_cls, lambda _a=adapter: _a)
    return injection


def _detect_implicit_sinks(
    adapters: dict[str, object],
    adapter_instances: dict[str, object],
    steps: list[StepSpec],
) -> list[tuple[str, list[type[object]]]]:
    # Identify adapters that consume models but are not injected into any node.
    implicit: list[tuple[str, list[type[object]]]] = []
    used_instances = _collect_used_adapters(steps)

    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        consumes = _resolve_consumes(cfg.get("consumes"))
        if not consumes:
            continue
        adapter = adapter_instances.get(role)
        if adapter is None:
            continue
        # Compare by identity to avoid unhashable adapters (e.g., dict-based stubs).
        if id(adapter) in used_instances:
            continue
        implicit.append((role, consumes))
    return implicit


def _emit_implicit_sink_diagnostics(
    config: dict[str, object],
    steps: list[StepSpec],
    *,
    adapter_instances: dict[str, object],
    trace_recorder: TraceRecorder | None,
    trace_sink: TraceSink | None,
    run_id: str,
    scenario_id: str,
) -> None:
    # Emit diagnostics for implicit sinks (Ports and adapters model §5.2).
    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        return
    implicit = _detect_implicit_sinks(adapters, adapter_instances, steps)
    if not implicit:
        return

    for role, consumes in implicit:
        consume_names = ", ".join(_token_label(token) for token in consumes)
        note = f"Implicit sink adapter '{role}' attached for consumes: {consume_names}"
        warnings.warn(note)
        if trace_recorder is None or trace_sink is None:
            continue
        trace_sink.emit(_diagnostic_record(note, run_id=run_id, scenario_id=scenario_id))


def _resolve_consumes(raw: object) -> list[type[object]]:
    # Resolve adapter consumes list (string paths or types).
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("adapter consumes must be a list")
    tokens: list[type[object]] = []
    for item in raw:
        if isinstance(item, type):
            tokens.append(item)
        elif isinstance(item, str):
            tokens.append(_resolve_symbol(item))
        else:
            raise ValueError("adapter consumes entries must be types or import paths")
    return tokens


def _collect_used_adapters(steps: list[StepSpec]) -> set[int]:
    # Collect adapter instance ids referenced by step objects (identity match).
    used: set[int] = set()
    for spec in steps:
        used.update(_iter_adapter_holders(spec.step))
    return used


def _iter_adapter_holders(step: object) -> set[int]:
    # Inspect step instance or bound method owner for injected adapters.
    holders: list[object] = []
    if hasattr(step, "__self__") and getattr(step, "__self__") is not None:
        holders.append(getattr(step, "__self__"))
    else:
        holders.append(step)

    found: set[int] = set()
    for holder in holders:
        for value in _iter_attr_values(holder):
            found.add(id(value))
    return found


def _iter_attr_values(obj: object) -> list[object]:
    # Extract values from dataclass fields or __dict__ for identity checks.
    values: list[object] = []
    if hasattr(obj, "__dataclass_fields__"):
        for name in obj.__dataclass_fields__:  # type: ignore[attr-defined]
            values.append(getattr(obj, name))
    else:
        values.extend(list(getattr(obj, "__dict__", {}).values()))
    return values


def _diagnostic_record(note: str, *, run_id: str, scenario_id: str) -> TraceRecord:
    # Build a TraceRecord for runtime diagnostics (Trace runtime spec).
    now = datetime.now(tz=UTC)
    msg_in = MessageSignature(type_name="RuntimeDiagnostic", identity=note, hash=None)
    return TraceRecord(
        trace_id=run_id,
        scenario=scenario_id,
        line_no=None,
        step_index=-1,
        step_name="runtime_diagnostic",
        work_index=0,
        t_enter=now,
        t_exit=now,
        duration_ms=0.0,
        msg_in=msg_in,
        msg_out=(),
        msg_out_count=0,
        ctx_before=None,
        ctx_after=None,
        ctx_diff=None,
        status="error",
        error=ErrorInfo(type="runtime_diagnostic", message=note, where="runtime"),
    )


def _token_label(token: type[object]) -> str:
    return getattr(token, "__name__", repr(token))


def _build_tracing(runtime: dict[str, object]) -> tuple[TraceRecorder | None, TraceSink | None]:
    # Build trace recorder/sink from runtime.tracing config (Trace spec).
    tracing = runtime.get("tracing")
    if not isinstance(tracing, dict) or not tracing.get("enabled"):
        return None, None

    signature = tracing.get("signature", {})
    context_diff = tracing.get("context_diff", {})
    if not isinstance(signature, dict):
        signature = {}
    if not isinstance(context_diff, dict):
        context_diff = {}

    recorder = TraceRecorder(
        signature_mode=str(signature.get("mode", "type_only")),
        context_diff_mode=str(context_diff.get("mode", "none")),
        context_diff_whitelist=list(context_diff.get("whitelist", []))
        if isinstance(context_diff.get("whitelist", []), list)
        else None,
    )

    sink_cfg = tracing.get("sink")
    if not isinstance(sink_cfg, dict):
        return recorder, None

    kind = sink_cfg.get("kind")
    if kind == "stdout":
        return recorder, StdoutTraceSink()
    if kind == "jsonl":
        jsonl = sink_cfg.get("jsonl", {})
        if not isinstance(jsonl, dict):
            raise ValueError("runtime.tracing.sink.jsonl must be a mapping")
        path = jsonl.get("path")
        if not isinstance(path, str):
            raise ValueError("runtime.tracing.sink.jsonl.path must be a string")
        return (
            recorder,
            JsonlTraceSink(
                path=Path(path),
                write_mode=str(jsonl.get("write_mode", "line")),
                flush_every_n=int(jsonl.get("flush_every_n", 1)),
                flush_every_ms=jsonl.get("flush_every_ms"),
                fsync_every_n=jsonl.get("fsync_every_n"),
            ),
        )
    return recorder, None


def _scenario_name(config: dict[str, object]) -> str:
    scenario = config.get("scenario", {})
    if isinstance(scenario, dict):
        name = scenario.get("name")
        if isinstance(name, str):
            return name
    return "scenario"
