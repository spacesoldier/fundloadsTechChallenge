from __future__ import annotations

import argparse
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stream-kernel")
    parser.add_argument("--config", required=True)
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--tracing", choices=["enable", "disable"])
    parser.add_argument("--trace-path")
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def apply_cli_overrides(config: dict[str, object], args: argparse.Namespace) -> None:
    # Override adapter paths if explicitly provided via CLI flags.
    adapters = config.setdefault("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")

    if args.input:
        _set_adapter_path(adapters, "input_source", args.input)
    if args.output:
        _set_adapter_path(adapters, "output_sink", args.output)

    if args.tracing or args.trace_path:
        runtime = config.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("runtime must be a mapping")
        tracing = runtime.setdefault("tracing", {})
        if not isinstance(tracing, dict):
            raise ValueError("runtime.tracing must be a mapping")
        if args.tracing is not None:
            tracing["enabled"] = args.tracing == "enable"
        elif "enabled" not in tracing:
            tracing["enabled"] = True
        if args.trace_path:
            sink = tracing.setdefault("sink", {"name": "trace_jsonl", "settings": {}})
            if not isinstance(sink, dict):
                raise ValueError("runtime.tracing.sink must be a mapping")
            # CLI trace path always selects the JSONL tracing sink explicitly.
            sink["name"] = "trace_jsonl"
            settings = sink.setdefault("settings", {})
            if not isinstance(settings, dict):
                raise ValueError("runtime.tracing.sink.settings must be a mapping")
            settings["path"] = args.trace_path
            # Keep adapter selection discovery-driven: ensure sink adapter role exists in adapters config.
            trace_adapter = adapters.setdefault("trace_jsonl", {})
            if not isinstance(trace_adapter, dict):
                raise ValueError("adapters.trace_jsonl must be a mapping")
            trace_settings = trace_adapter.setdefault("settings", {})
            if not isinstance(trace_settings, dict):
                raise ValueError("adapters.trace_jsonl.settings must be a mapping")
            trace_settings["path"] = args.trace_path
            trace_adapter.setdefault("binds", [])


def _set_adapter_path(adapters: dict[str, object], name: str, path: str) -> None:
    entry = adapters.setdefault(name, {})
    if not isinstance(entry, dict):
        raise ValueError(f"adapters.{name} must be a mapping")
    settings = entry.setdefault("settings", {})
    if not isinstance(settings, dict):
        raise ValueError(f"adapters.{name}.settings must be a mapping")
    settings["path"] = path
