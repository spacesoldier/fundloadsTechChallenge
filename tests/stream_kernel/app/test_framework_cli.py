from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.app.cli import apply_cli_overrides, build_parser, parse_args


def _config() -> dict[str, object]:
    return {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"strict": True, "tracing": {"enabled": False}},
        "nodes": {},
        "adapters": {
            "input_source": {"kind": "file", "path": "input.txt"},
            "output_sink": {"kind": "file", "path": "output.txt"},
        },
    }


def test_parse_args_reads_core_flags() -> None:
    args = parse_args(
        [
            "--config",
            "cfg.yml",
            "--input",
            "in.ndjson",
            "--output",
            "out.txt",
            "--tracing",
            "enable",
            "--trace-path",
            "trace.jsonl",
        ]
    )
    assert args.config == "cfg.yml"
    assert args.input == "in.ndjson"
    assert args.output == "out.txt"
    assert args.tracing == "enable"
    assert args.trace_path == "trace.jsonl"


def test_apply_cli_overrides_updates_paths_and_tracing() -> None:
    cfg = _config()
    args = SimpleNamespace(
        input="override_input.ndjson",
        output="override_output.txt",
        tracing="enable",
        trace_path="override_trace.jsonl",
    )

    apply_cli_overrides(cfg, args)

    adapters = cfg["adapters"]
    assert adapters["input_source"]["settings"]["path"] == "override_input.ndjson"
    assert adapters["output_sink"]["settings"]["path"] == "override_output.txt"
    assert cfg["runtime"]["tracing"]["enabled"] is True
    assert cfg["runtime"]["tracing"]["sink"]["jsonl"]["path"] == "override_trace.jsonl"


def test_build_parser_declares_known_flags() -> None:
    parser = build_parser()
    flags = {action.dest for action in parser._actions}
    assert "config" in flags
    assert "input" in flags
    assert "output" in flags
    assert "tracing" in flags
    assert "trace_path" in flags
