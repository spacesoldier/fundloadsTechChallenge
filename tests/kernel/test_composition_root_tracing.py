from __future__ import annotations

import json
from pathlib import Path

import pytest

# Composition root should wire tracing per config (Trace spec §9).
from dataclasses import dataclass

from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.config.loader import load_config
from fund_load.domain.messages import RawLine
from fund_load.kernel.composition_root import build_runtime_from_app_config


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _OutputSink:
    # OutputSink stub collects lines for WriteOutput.
    lines: list[str]

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        pass


def test_composition_root_builds_trace_sink_from_config(tmp_path: Path) -> None:
    # Trace sink must be created when tracing.enabled=true (Trace spec §9).
    config_path = _repo_root() / "src" / "fund_load" / "baseline_config.yml"
    config = load_config(config_path)
    trace_path = tmp_path / "trace.jsonl"

    # Override tracing settings for the test to avoid touching repo files.
    assert config.tracing is not None
    config.tracing.enabled = True
    assert config.tracing.sink is not None
    assert config.tracing.sink.jsonl is not None
    config.tracing.sink.jsonl.path = str(trace_path)
    config.tracing.context_diff.mode = "whitelist"
    config.tracing.context_diff.whitelist = ["line_no"]

    runtime = build_runtime_from_app_config(
        config=config,
        wiring={
            "prime_checker": SievePrimeChecker.from_max(0),
            "window_store": InMemoryWindowStore(),
            "output_sink": _OutputSink([]),
        },
    )
    runtime.runner.run(
        [
            RawLine(
                line_no=1,
                raw_text='{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}',
            )
        ],
        output_sink=lambda _: None,
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(config.pipeline.steps)
    first = json.loads(lines[0])
    last = json.loads(lines[-1])
    assert first["step_name"] == "parse_load_attempt"
    assert last["step_name"] == "write_output"
    assert first["ctx_before"] == {"line_no": 1}
