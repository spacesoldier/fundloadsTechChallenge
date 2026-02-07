from __future__ import annotations

import importlib
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fund_load.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from fund_load.ports.trace_sink import TraceSink
from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.adapters.registry import AdapterRegistry
from stream_kernel.app.cli import apply_cli_overrides, parse_args
from stream_kernel.application_context import ApplicationContext
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.execution.planning import build_execution_plan
from stream_kernel.execution.runner import SyncRunner
from stream_kernel.integration.context_store import InMemoryContextStore
from stream_kernel.integration.routing_port import RoutingPort
from stream_kernel.integration.work_queue import InMemoryWorkQueue
from stream_kernel.kernel.context import Context, ContextFactory
from stream_kernel.kernel.dag import NodeContract
from stream_kernel.kernel.scenario import StepSpec
from stream_kernel.kernel.trace import ErrorInfo, MessageSignature, TraceRecord, TraceRecorder
from stream_kernel.routing.envelope import Envelope


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
        apply_cli_overrides(config, args)

    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")
    _reject_runtime_pipeline(runtime)
    if discovery_modules is None:
        discovered_modules = runtime.get("discovery_modules", [])
        if not isinstance(discovered_modules, list) or not all(
            isinstance(item, str) for item in discovered_modules
        ):
            raise ValueError("runtime.discovery_modules must be a list of strings")
        discovery_modules = discovered_modules

    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")

    if adapter_registry is None and adapter_bindings is not None:
        raise ValueError("adapter_bindings override requires adapter_registry override")
    if adapter_registry is None:
        adapter_registry, resolved_bindings = _resolve_runtime_adapters(
            adapters=adapters,
            discovery_modules=discovery_modules,
        )
        if adapter_bindings is None:
            adapter_bindings = resolved_bindings
    if adapter_bindings is None:
        adapter_bindings = _build_adapter_bindings(adapters, adapter_registry)

    adapter_instances = _build_adapter_instances_from_registry(adapters, adapter_registry)
    # Build injection registry from provided bindings using shared adapter instances.
    injection_registry = _build_injection_registry_from_bindings(adapter_instances, adapter_bindings)

    ctx = ApplicationContext()
    modules = [importlib.import_module(name) for name in discovery_modules]
    ctx.discover(modules)
    adapter_contracts = _build_adapter_contracts(adapters, adapter_registry=adapter_registry)
    dag = ctx.preflight(
        strict=bool(runtime.get("strict", True)),
        extra_contracts=adapter_contracts,
    )
    consumer_registry = ctx.build_consumer_registry()
    step_names = _resolve_step_names(dag)

    scenario_name = _scenario_name(config)
    scenario = ctx.build_scenario(
        scenario_id=scenario_name,
        step_names=step_names,
        wiring={
            "injection_registry": injection_registry,
            "consumer_registry": consumer_registry,
            "config": config,
            "strict": bool(runtime.get("strict", True)),
        },
    )

    # Source bootstrap: any read-capable adapter can provide input payloads.
    inputs = _read_inputs_from_sources(adapter_instances)

    trace_recorder, trace_sink = _build_tracing(runtime)
    _emit_implicit_sink_diagnostics(
        config,
        scenario.steps,
        adapter_instances=adapter_instances,
        adapter_registry=adapter_registry,
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
        run_id=run_id,
        scenario_id=scenario_name,
    )
    strict = bool(runtime.get("strict", True))
    _run_with_sync_runner(
        scenario=scenario,
        inputs=inputs,
        consumer_registry=consumer_registry,
        strict=strict,
        run_id=run_id,
        scenario_id=scenario_name,
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
    )
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
    # Generic framework entrypoint using discovered adapter kinds from config.
    args = parse_args(argv or [])
    config = validate_newgen_config(load_yaml_config(Path(args.config)))
    apply_cli_overrides(config, args)
    return run_with_config(config, argv_overrides=None, run_id="run")


def _resolve_runtime_adapters(
    *,
    adapters: dict[str, object],
    discovery_modules: list[str],
) -> tuple[AdapterRegistry, dict[str, object]]:
    # Discover adapters by name and bind them to equally named YAML roles.
    modules = [importlib.import_module(name) for name in discovery_modules]
    discovered = discover_adapters(modules)

    registry = AdapterRegistry()
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        if "kind" in cfg:
            raise ValueError(
                f"adapters.{role}.kind is not supported; adapter name is defined by adapters.{role}"
            )
        factory = discovered.get(role)
        if factory is None:
            raise ValueError(f"Unknown adapter name: {role}")
        # Reuse role as registry key-kind to keep AdapterRegistry API unchanged.
        registry.register(role, role, factory)

    bindings = _build_adapter_bindings(adapters, registry)
    return registry, bindings


def _build_adapter_bindings(
    adapters: dict[str, object],
    registry: AdapterRegistry,
) -> dict[str, object]:
    # Convert config binds (stable port names) into typed injection bindings from adapter metadata.
    bindings: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = registry.get_meta(role, role)
        if meta is None:
            continue

        requested = cfg.get("binds", [])
        if not isinstance(requested, list):
            raise ValueError(f"adapters.{role}.binds must be a list")
        if not all(isinstance(item, str) for item in requested):
            raise ValueError(f"adapters.{role}.binds entries must be strings")

        resolved: list[tuple[str, type[Any]]] = []
        for port_type in requested:
            matches = [(ptype, dtype) for (ptype, dtype) in meta.binds if ptype == port_type]
            if not matches:
                raise ValueError(
                    f"adapters.{role}.binds includes unsupported port_type '{port_type}' for adapter '{role}'"
                )
            resolved.extend(matches)
        if resolved:
            bindings[role] = resolved
    return bindings


def _run_with_sync_runner(
    *,
    scenario,
    inputs,
    consumer_registry,
    strict: bool,
    run_id: str,
    scenario_id: str,
    trace_recorder: TraceRecorder | None,
    trace_sink: TraceSink | None,
) -> None:
    # Build execution components (Execution runtime and routing integration §6).
    work_queue = InMemoryWorkQueue()
    context_store = InMemoryContextStore()
    routing_port = RoutingPort(registry=consumer_registry, strict=strict)

    trace_contexts: dict[str, Context] = {}
    nodes = _build_targeted_nodes(
        scenario.steps,
        run_id=run_id,
        scenario_id=scenario_id,
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
        trace_contexts=trace_contexts,
    )
    runner = SyncRunner(
        nodes=nodes,
        work_queue=work_queue,
        context_store=context_store,
        routing_port=routing_port,
    )

    # Preserve deterministic semantics: each input message is processed end-to-end before next input.
    for index, payload in enumerate(inputs, start=1):
        trace_id = _trace_id(run_id, payload, index)
        context_store.put(trace_id, _initial_context(payload, trace_id))
        deliveries = routing_port.route([payload])
        for target_name, routed_payload in deliveries:
            work_queue.push(Envelope(payload=routed_payload, target=target_name, trace_id=trace_id))
        runner.run()
    if trace_sink is not None:
        # Mirror legacy runner behavior: flush/close trace sink at end of run.
        trace_sink.flush()
        trace_sink.close()


def _read_inputs_from_sources(adapter_instances: dict[str, object]) -> list[object]:
    # Build deterministic startup payload list from read-capable adapters.
    payloads: list[object] = []
    source_roles: list[str] = []
    for role in sorted(adapter_instances.keys()):
        adapter = adapter_instances[role]
        read = getattr(adapter, "read", None)
        if not callable(read):
            continue
        source_roles.append(role)
        payloads.extend(read())
    if not source_roles:
        raise ValueError("at least one source adapter with read() must be configured")
    return payloads


def _build_targeted_nodes(
    step_specs: list[StepSpec],
    *,
    run_id: str,
    scenario_id: str,
    trace_recorder: TraceRecorder | None,
    trace_sink: TraceSink | None,
    trace_contexts: dict[str, Context],
) -> dict[str, object]:
    # Wrap scenario steps for tracing while preserving native routing semantics.
    # Actual fan-out/target resolution is handled by Router via consumes/emits contracts.
    nodes: dict[str, object] = {}
    for idx, spec in enumerate(step_specs):
        def _make_step(step: object, step_name: str, step_index: int):
            def _wrapped(msg: object, ctx: object | None) -> list[object]:
                raw_ctx = ctx if isinstance(ctx, dict) else {}
                node_ctx = _node_ctx(raw_ctx)
                trace_ctx: Context | None = None
                span = None
                if trace_recorder is not None:
                    trace_id = _extract_trace_id(raw_ctx)
                    if trace_id is not None:
                        line_no = _extract_line_no(raw_ctx)
                        trace_ctx = trace_contexts.setdefault(
                            trace_id,
                            Context(
                                trace_id=trace_id,
                                run_id=run_id,
                                scenario_id=scenario_id,
                                line_no=line_no,
                                received_at=datetime.now(tz=UTC),
                            ),
                        )
                        span = trace_recorder.begin(
                            ctx=trace_ctx,
                            step_name=step_name,
                            step_index=step_index,
                            work_index=0,
                            msg_in=msg,
                        )

                try:
                    outputs = list(step(msg, node_ctx))
                except Exception as exc:
                    if trace_recorder is not None and span is not None and trace_ctx is not None:
                        record = trace_recorder.finish(
                            ctx=trace_ctx,
                            span=span,
                            msg_out=[],
                            status="error",
                            error=ErrorInfo(
                                type=type(exc).__name__,
                                message=str(exc),
                                where=step_name,
                                stack=None,
                            ),
                        )
                        if trace_sink is not None:
                            trace_sink.emit(record)
                    raise

                if trace_recorder is not None and span is not None and trace_ctx is not None:
                    record = trace_recorder.finish(
                        ctx=trace_ctx,
                        span=span,
                        msg_out=outputs,
                        status="ok",
                        error=None,
                    )
                    if trace_sink is not None:
                        trace_sink.emit(record)
                return outputs

            return _wrapped

        nodes[spec.name] = _make_step(spec.step, spec.name, idx)
    return nodes


def _resolve_step_names(dag: object | None) -> list[str]:
    # Runtime order is execution-plan driven and derived from DAG contracts.
    if dag is None:
        return []
    if not hasattr(dag, "nodes") or not hasattr(dag, "edges"):
        raise ValueError("preflight must return a Dag-like object with nodes and edges")
    # Adapter contracts participate in DAG validation but are not executable scenario steps.
    return [name for name in build_execution_plan(dag) if not name.startswith("adapter:")]


def _build_adapter_contracts(
    adapters: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None,
) -> list[NodeContract]:
    # Adapter contracts model source/sink edges in preflight DAG.
    # Contracts are sourced from @adapter metadata on factory callables.
    contracts: list[NodeContract] = []
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = _resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        if meta is None:
            continue
        consumes = list(meta.consumes)
        emits = list(meta.emits)
        if not consumes and not emits:
            continue
        contracts.append(NodeContract(name=f"adapter:{role}", consumes=consumes, emits=emits))
    return contracts


def _resolve_adapter_meta(
    role: str,
    cfg: dict[str, object],
    *,
    adapter_registry: AdapterRegistry | None,
):
    # Resolve adapter metadata from AdapterRegistry role/kind registrations.
    if adapter_registry is not None:
        meta = adapter_registry.get_meta(role, role)
        if meta is not None:
            return meta
    return None


def _reject_runtime_pipeline(runtime: dict[str, object]) -> None:
    if "pipeline" in runtime:
        raise ValueError(
            "runtime.pipeline is no longer supported; rely on consumes/emits routing contracts"
        )


def _trace_id(run_id: str, payload: object, index: int) -> str:
    # Keep per-message trace ids deterministic for context lookups.
    line_no = getattr(payload, "line_no", None)
    if isinstance(line_no, int):
        return f"{run_id}:{line_no}"
    return f"{run_id}:{index}"


def _initial_context(payload: object, trace_id: str) -> dict[str, object]:
    # Minimal metadata view exposed to nodes via ContextStore.
    line_no = getattr(payload, "line_no", None)
    if isinstance(line_no, int):
        return {"line_no": line_no, "__trace_id": trace_id}
    return {"__trace_id": trace_id}


def _extract_trace_id(ctx: dict[str, object]) -> str | None:
    trace_id = ctx.get("__trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    return None


def _extract_line_no(ctx: dict[str, object]) -> int | None:
    line_no = ctx.get("line_no")
    if isinstance(line_no, int):
        return line_no
    return None


def _node_ctx(ctx: dict[str, object]) -> dict[str, object]:
    # Hide framework-internal metadata from user nodes.
    return {key: value for key, value in ctx.items() if not key.startswith("__")}


def _build_adapter_instances_from_registry(
    adapters: dict[str, object],
    registry: AdapterRegistry,
) -> dict[str, object]:
    # Build adapter instances using AdapterRegistry (role/kind), used for diagnostics.
    instances: dict[str, object] = {}
    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"adapters.{role} must be a mapping")
        # New contract: adapter is selected by YAML role name; bridge to registry API via implicit kind=role.
        effective_cfg = dict(cfg)
        effective_cfg.setdefault("kind", role)
        instances[role] = registry.build(role, effective_cfg)
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


def _detect_implicit_sinks(
    adapters: dict[str, object],
    adapter_instances: dict[str, object],
    steps: list[StepSpec],
    *,
    adapter_registry: AdapterRegistry | None,
) -> list[tuple[str, list[type[object]]]]:
    # Identify adapters that consume models but are not injected into any node.
    implicit: list[tuple[str, list[type[object]]]] = []
    used_instances = _collect_used_adapters(steps)

    for role, cfg in adapters.items():
        if not isinstance(cfg, dict):
            continue
        meta = _resolve_adapter_meta(role, cfg, adapter_registry=adapter_registry)
        if meta is None:
            continue
        consumes = list(meta.consumes)
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
    adapter_registry: AdapterRegistry | None,
    trace_recorder: TraceRecorder | None,
    trace_sink: TraceSink | None,
    run_id: str,
    scenario_id: str,
) -> None:
    # Emit diagnostics for implicit sinks (Ports and adapters model §5.2).
    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        return
    implicit = _detect_implicit_sinks(
        adapters,
        adapter_instances,
        steps,
        adapter_registry=adapter_registry,
    )
    if not implicit:
        return

    for role, consumes in implicit:
        consume_names = ", ".join(_token_label(token) for token in consumes)
        note = f"Implicit sink adapter '{role}' attached for consumes: {consume_names}"
        warnings.warn(note)
        if trace_recorder is None or trace_sink is None:
            continue
        trace_sink.emit(_diagnostic_record(note, run_id=run_id, scenario_id=scenario_id))



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
