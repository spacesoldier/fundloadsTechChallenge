from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

# CLI behavior follows docs/implementation/kernel/Composition Root Spec.md.
from fund_load.app.cli import apply_output_override, parse_args, run
from fund_load.config.loader import load_config
from fund_load.usecases.config_models import AppConfig


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _minimal_config() -> AppConfig:
    # Minimal config satisfies required sections (Configuration spec).
    return AppConfig.model_validate(
        {
            "version": 1,
            "scenario": {"name": "baseline"},
            "pipeline": {"steps": [{"name": "parse_load_attempt"}]},
            "policies": {
                "pack": "baseline",
                "evaluation_order": ["daily_attempt_limit"],
                "limits": {"daily_amount": "1.00", "weekly_amount": "1.00", "daily_attempts": 1},
            },
            "features": {
                "enabled": True,
                "monday_multiplier": {"enabled": False, "multiplier": "2.0", "apply_to": "amount"},
                "prime_gate": {"enabled": False, "global_per_day": 1, "amount_cap": "9999.00"},
            },
            "windows": {
                "daily_attempts": {"enabled": True},
                "daily_accepted_amount": {"enabled": True},
                "weekly_accepted_amount": {"enabled": True},
                "daily_prime_gate": {"enabled": False},
            },
            "output": {"file": "output.txt"},
        }
    )


def test_parse_args_reads_flags() -> None:
    # CLI must parse config/input/output and tracing flags (Composition Root spec §6.1).
    args = parse_args(
        [
            "--config",
            "cfg.yml",
            "--input",
            "input.txt",
            "--output",
            "out.txt",
            "--tracing",
            "enable",
            "--trace-path",
            "trace.jsonl",
        ]
    )
    assert args.config == "cfg.yml"
    assert args.input == "input.txt"
    assert args.output == "out.txt"
    assert args.tracing == "enable"
    assert args.trace_path == "trace.jsonl"


def test_apply_output_override_updates_config() -> None:
    # Output overrides should win over config values (Composition Root spec §3.1).
    config = _minimal_config()
    args = SimpleNamespace(output="override.txt")
    apply_output_override(config, args)
    assert config.output.file_path == "override.txt"


def test_cli_run_writes_output_and_trace(tmp_path: Path) -> None:
    # End-to-end CLI run should honor output/trace overrides (Composition Root spec §6.1).
    config_path = _repo_root() / "src" / "fund_load" / "baseline_config.yml"
    input_path = tmp_path / "input.ndjson"
    output_path = tmp_path / "output.txt"
    trace_path = tmp_path / "trace.jsonl"

    input_path.write_text(
        '{"id":"1","customer_id":"10","load_amount":"$1.00","time":"2025-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    exit_code = run(
        [
            "--config",
            str(config_path),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--tracing",
            "enable",
            "--trace-path",
            str(trace_path),
        ]
    )
    assert exit_code == 0
    output_lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(output_lines) == 1
    assert json.loads(output_lines[0]) == {"id": "1", "customer_id": "10", "accepted": True}

    config = load_config(config_path)
    trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(trace_lines) == len(config.pipeline.steps)
