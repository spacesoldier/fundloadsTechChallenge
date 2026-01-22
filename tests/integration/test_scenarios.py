from __future__ import annotations

import json
from collections.abc import Iterable
from itertools import islice
from pathlib import Path

import pytest

from fund_load.adapters.input_source import FileInputSource
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.config.loader import load_config
from fund_load.kernel.context import ContextFactory
from fund_load.kernel.runner import Runner
from fund_load.kernel.scenario_builder import ScenarioBuilder
from fund_load.usecases.config_models import AppConfig, StepDecl
from fund_load.usecases.messages import OutputLine
from fund_load.usecases.wiring import build_step_registry


class _CollectingOutputSink:
    # OutputSink test double: collects output lines in order (docs/implementation/ports/OutputSink.md).
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def close(self) -> None:
        # No-op; real adapters release resources here.
        pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_app_config(name: str) -> AppConfig:
    # Configs are stored under src/fund_load (docs/implementation/architecture/Configuration spec.md).
    config_path = _repo_root() / "src" / "fund_load" / name
    return load_config(config_path)


def _steps_from_config(steps: Iterable[StepDecl], *, include_write_output: bool) -> list[dict[str, object]]:
    # ScenarioBuilder expects "config" entries; StepDecl exposes config as the canonical field.
    mapped: list[dict[str, object]] = []
    for step in steps:
        if not include_write_output and step.name == "write_output":
            continue
        mapped.append({"name": step.name, "config": step.config})
    return mapped


def _run_scenario(
    *,
    config: AppConfig,
    input_lines: Iterable[object],
    include_write_output: bool,
) -> tuple[list[str], list[str]]:
    records = list(input_lines)
    prime_checker = _prime_checker_for_input(records)
    sink = _CollectingOutputSink()
    wiring = {
        "prime_checker": prime_checker,
        "window_store": InMemoryWindowStore(),
        "output_sink": sink,
    }
    registry = build_step_registry(config, wiring=wiring)
    steps_cfg = _steps_from_config(config.pipeline.steps, include_write_output=include_write_output)
    scenario = ScenarioBuilder(registry).build(
        scenario_id=config.scenario.name,
        steps=steps_cfg,
        wiring=wiring,
    )
    runner = Runner(
        scenario=scenario,
        context_factory=ContextFactory("test", config.scenario.name),
    )
    output_lines: list[str] = []

    def _runner_sink(msg: object) -> None:
        if isinstance(msg, OutputLine):
            output_lines.append(msg.json_text)

    # Runner output sink is unused when the pipeline ends with WriteOutput.
    runner.run(inputs=records, output_sink=_runner_sink)
    return (sink.lines if include_write_output else output_lines), steps_cfg


def _read_input_lines(limit: int | None = None) -> list[object]:
    # Input is read via FileInputSource to exercise the port/adapter (docs/implementation/ports/InputSource.md).
    input_path = _repo_root() / "docs" / "analysis" / "data" / "assets" / "input.txt"
    source = FileInputSource(input_path)
    records = list(source.read())
    return records if limit is None else list(islice(records, limit))


def _read_output_lines(path: Path, *, limit: int | None = None) -> list[str]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    return raw_lines if limit is None else list(islice(raw_lines, limit))


def _parse_json_lines(lines: Iterable[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in lines]


def _prime_checker_for_input(records: Iterable[object]) -> SievePrimeChecker:
    # Prime checker uses a sieve sized to the observed id range (docs/implementation/ports/PrimeChecker.md).
    max_id = 0
    for record in records:
        raw_text = getattr(record, "raw_text", "")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        id_value = str(payload.get("id", "")).strip()
        if id_value.isdigit():
            max_id = max(max_id, int(id_value))
    return SievePrimeChecker.from_max(max_id)


def test_minimal_fixture_order_and_schema() -> None:
    # Integration test per docs/Developer instructions.md: small fixture, order + schema.
    config = _load_app_config("baseline_config.yml")
    input_lines = _read_input_lines(limit=10)

    output_lines, steps_cfg = _run_scenario(
        config=config,
        input_lines=input_lines,
        include_write_output=False,
    )

    assert [step["name"] for step in steps_cfg][-1] == "format_output"
    assert len(output_lines) == len(input_lines)

    output_objs = _parse_json_lines(output_lines)
    # Output schema is id/customer_id/accepted; key order is handled by FormatOutput tests.
    for obj in output_objs:
        assert obj.keys() == {"id", "customer_id", "accepted"}
        assert isinstance(obj["id"], str)
        assert isinstance(obj["customer_id"], str)
        assert isinstance(obj["accepted"], bool)

    # Order validation: compare the same slice against reference output (docs/analysis/data/assets/output.txt).
    # The reference output file uses a different key order than Step 07 spec, so we compare dicts.
    expected_path = _repo_root() / "docs" / "analysis" / "data" / "assets" / "output.txt"
    expected_lines = _read_output_lines(expected_path, limit=10)
    assert _parse_json_lines(output_lines) == _parse_json_lines(expected_lines)


@pytest.mark.parametrize(
    ("config_name", "expected_output_name"),
    [
        ("baseline_config.yml", "output.txt"),
        ("experiment_config.yml", "output_exp_mp.txt"),
    ],
)
def test_reference_outputs_match_assets(config_name: str, expected_output_name: str) -> None:
    # Compare full output to reference assets (docs/analysis/data/Reference output generation.md).
    # Note: reference assets show a different JSON key order than Step 07 spec, so we compare dicts.
    config = _load_app_config(config_name)
    input_lines = _read_input_lines()

    output_lines, _ = _run_scenario(
        config=config,
        input_lines=input_lines,
        include_write_output=True,
    )

    expected_path = (
        _repo_root() / "docs" / "analysis" / "data" / "assets" / expected_output_name
    )
    expected_lines = _read_output_lines(expected_path)

    assert len(output_lines) == len(expected_lines)
    assert _parse_json_lines(output_lines) == _parse_json_lines(expected_lines)
