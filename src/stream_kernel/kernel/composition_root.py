from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stream_kernel.kernel.context import ContextFactory
from stream_kernel.kernel.runner import Runner
from stream_kernel.kernel.scenario import Scenario
from stream_kernel.kernel.scenario_builder import ScenarioBuilder
from stream_kernel.kernel.step_registry import StepRegistry
from stream_kernel.kernel.trace import TraceRecorder
from fund_load.usecases.config_models import AppConfig, TracingConfig
from fund_load.usecases.wiring import build_step_registry
from fund_load.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from fund_load.ports.trace_sink import TraceSink


@dataclass(frozen=True, slots=True)
class AppRuntime:
    # AppRuntime is a small bundle for runner + scenario (Composition Root spec).
    runner: Runner
    scenario: Scenario


def build_runtime(*, config: dict[str, object], wiring: dict[str, object]) -> AppRuntime:
    # Composition root wires registry, builder, and runner (docs/implementation/kernel/Composition Root Spec.md).
    steps_cfg = config.get("steps")
    scenario_id = config.get("scenario_id")
    if not isinstance(steps_cfg, list) or not isinstance(scenario_id, str):
        raise ValueError("Invalid config: scenario_id and steps are required")

    registry = StepRegistry()
    # Steps are provided via wiring in tests; real wiring will use adapters and step factories.
    for name, factory in wiring.get("steps", {}).items():
        registry.register(name, factory)

    scenario = ScenarioBuilder(registry).build(
        scenario_id=scenario_id,
        steps=steps_cfg,
        wiring=wiring,
    )
    runner = Runner(scenario=scenario, context_factory=ContextFactory("run", scenario_id))
    return AppRuntime(runner=runner, scenario=scenario)


def build_runtime_from_app_config(
    *,
    config: AppConfig,
    wiring: dict[str, object],
    run_id: str = "run",
) -> AppRuntime:
    # AppConfig composition uses usecase wiring + tracing config (Composition Root spec + Trace spec §9).
    registry = build_step_registry(config, wiring)
    steps_cfg = [{"name": step.name, "config": step.config} for step in config.pipeline.steps]
    scenario = ScenarioBuilder(registry).build(
        scenario_id=config.scenario.name,
        steps=steps_cfg,
        wiring=wiring,
    )
    trace_recorder, trace_sink = _build_tracing(config.tracing)
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory(run_id, config.scenario.name),
        trace_recorder=trace_recorder,
        trace_sink=trace_sink,
    )
    return AppRuntime(runner=runner, scenario=scenario)


def _build_tracing(tracing: TracingConfig | None) -> tuple[TraceRecorder | None, TraceSink | None]:
    # Tracing is optional; when enabled, we create recorder + optional sink (Trace spec §2/§9).
    if tracing is None or not tracing.enabled:
        return None, None

    recorder = TraceRecorder(
        signature_mode=tracing.signature.mode,
        context_diff_mode=tracing.context_diff.mode,
        context_diff_whitelist=tracing.context_diff.whitelist,
    )

    if tracing.sink is None:
        # If no sink is configured, we still keep in-memory ctx.trace for debugging.
        return recorder, None

    if tracing.sink.kind == "stdout":
        return recorder, StdoutTraceSink()

    if tracing.sink.kind == "jsonl":
        jsonl = tracing.sink.jsonl
        assert jsonl is not None  # validated by config model
        return (
            recorder,
            JsonlTraceSink(
                path=Path(jsonl.path),
                write_mode=jsonl.write_mode,
                flush_every_n=jsonl.flush_every_n,
                flush_every_ms=jsonl.flush_every_ms,
                fsync_every_n=jsonl.fsync_every_n,
            ),
        )

    # OTel is not implemented yet; fail fast for unknown kinds.
    raise ValueError(f"Unsupported trace sink kind: {tracing.sink.kind}")
