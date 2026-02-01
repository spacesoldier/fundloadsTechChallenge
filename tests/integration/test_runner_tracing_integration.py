from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Integration tracing behavior is specified in docs/implementation/kernel/Trace and Context Change Log Spec.md.
from fund_load.adapters.trace_sinks import JsonlTraceSink
from stream_kernel.kernel.context import Context, ContextFactory
from stream_kernel.kernel.runner import Runner
from stream_kernel.kernel.scenario import Scenario, StepSpec
from stream_kernel.kernel.trace import TraceRecorder


@dataclass(frozen=True, slots=True)
class _Input:
    line_no: int
    value: int


def test_runner_tracing_integration_writes_trace_lines(tmp_path: Path) -> None:
    # Trace sink must receive records in the same deterministic order as ctx.trace (Trace spec §10.3/§5).
    ctxs = []

    def step1(msg:object, ctx: Context | None) -> list[object]:
        if ctx not in ctxs:
            ctxs.append(ctx)
        return [msg.value + 1]

    def step2(msg:object, ctx: Context | None) -> list[object]:
        return [msg]

    scenario = Scenario(
        scenario_id="trace-test",
        steps=[StepSpec(name="step1", step=step1), StepSpec(name="step2", step=step2)],
    )
    recorder = TraceRecorder(signature_mode="type_only", context_diff_mode="none")
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(path=trace_path, write_mode="line", flush_every_n=1, fsync_every_n=None)
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("run", "trace-test"),
        trace_recorder=recorder,
        trace_sink=sink,
    )

    runner.run([_Input(1, 10), _Input(2, 20)], output_sink=lambda _: None)

    assert len(ctxs) == 2
    assert len(ctxs[0].trace) == 2
    assert len(ctxs[1].trace) == 2

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    records = [json.loads(line) for line in lines]
    assert [r["line_no"] for r in records] == [1, 1, 2, 2]
    assert [r["step_index"] for r in records] == [0, 1, 0, 1]
