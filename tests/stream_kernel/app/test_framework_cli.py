from __future__ import annotations

from types import SimpleNamespace

import pytest

from stream_kernel.app.cli import apply_cli_overrides, build_parser, parse_args


def _config() -> dict[str, object]:
    return {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {"strict": True, "tracing": {"enabled": False}},
        "nodes": {},
        "adapters": {
            "input_source": {"path": "input.txt"},
            "output_sink": {"path": "output.txt"},
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
    assert cfg["runtime"]["tracing"]["sink"]["name"] == "trace_jsonl"
    assert cfg["runtime"]["tracing"]["sink"]["settings"]["path"] == "override_trace.jsonl"


def test_build_parser_declares_known_flags() -> None:
    parser = build_parser()
    flags = {action.dest for action in parser._actions}
    assert "config" in flags
    assert "input" in flags
    assert "output" in flags
    assert "tracing" in flags
    assert "trace_path" in flags


def test_apply_cli_overrides_requires_adapters_mapping() -> None:
    # CLI overrides must validate config structure (Configuration spec §2.1).
    args = SimpleNamespace(input="in.txt", output=None, tracing=None, trace_path=None)
    with pytest.raises(ValueError):
        apply_cli_overrides({"adapters": "nope"}, args)


def test_apply_cli_overrides_requires_runtime_mapping_when_tracing() -> None:
    # Tracing overrides expect runtime/tracing mappings (Trace runtime spec).
    args = SimpleNamespace(input=None, output=None, tracing="enable", trace_path=None)
    with pytest.raises(ValueError):
        apply_cli_overrides({"adapters": {}, "runtime": "nope"}, args)


def test_apply_cli_overrides_requires_tracing_mapping() -> None:
    # runtime.tracing must be a mapping when tracing overrides are applied.
    args = SimpleNamespace(input=None, output=None, tracing="enable", trace_path=None)
    cfg = {"adapters": {}, "runtime": {"tracing": "nope"}}
    with pytest.raises(ValueError):
        apply_cli_overrides(cfg, args)


def test_apply_cli_overrides_requires_tracing_sink_mapping() -> None:
    # Trace sink must be a mapping when trace-path is provided (Trace runtime spec).
    args = SimpleNamespace(input=None, output=None, tracing=None, trace_path="trace.jsonl")
    cfg = {"adapters": {}, "runtime": {"tracing": {"sink": "nope"}}}
    with pytest.raises(ValueError):
        apply_cli_overrides(cfg, args)


def test_apply_cli_overrides_requires_jsonl_mapping() -> None:
    # Trace sink settings must be a mapping in the canonical tracing sink shape.
    args = SimpleNamespace(input=None, output=None, tracing=None, trace_path="trace.jsonl")
    cfg = {"adapters": {}, "runtime": {"tracing": {"sink": {"name": "trace_jsonl", "settings": "nope"}}}}
    with pytest.raises(ValueError):
        apply_cli_overrides(cfg, args)


def test_apply_cli_overrides_requires_adapter_entry_mapping() -> None:
    # Adapter entries must be mappings for path override (Configuration spec §2.1).
    args = SimpleNamespace(input="in.txt", output=None, tracing=None, trace_path=None)
    with pytest.raises(ValueError):
        apply_cli_overrides({"adapters": {"input_source": "nope"}}, args)


def test_apply_cli_overrides_requires_adapter_settings_mapping() -> None:
    # Adapter settings must be a mapping for path override (Configuration spec §2.1).
    args = SimpleNamespace(input="in.txt", output=None, tracing=None, trace_path=None)
    with pytest.raises(ValueError):
        apply_cli_overrides({"adapters": {"input_source": {"settings": "nope"}}}, args)
