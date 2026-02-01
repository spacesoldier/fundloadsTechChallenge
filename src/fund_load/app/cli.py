from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from fund_load.adapters.input_source import FileInputSource
from fund_load.adapters.output_sink import FileOutputSink
from fund_load.adapters.prime_checker import SievePrimeChecker
from fund_load.adapters.window_store import InMemoryWindowStore
from fund_load.config.loader import load_config
from stream_kernel.kernel.composition_root import build_runtime_from_app_config
from fund_load.usecases.config_models import (
    AppConfig,
    TraceContextDiffConfig,
    TraceSignatureConfig,
    TraceSinkConfig,
    TraceSinkJsonlConfig,
    TracingConfig,
)

# NOTE: This CLI module is intentionally a thin wrapper around composition root wiring.
# In a future framework-style setup, this would be a reusable "application shell" package,
# and the list of CLI parameters would be declared/configured (not hardcoded per project).
# We keep this file in app/ to preserve the kernel boundary and make future extraction easier.


def build_parser() -> argparse.ArgumentParser:
    # CLI arguments are described in Composition Root spec §6.1.
    # Future direction: parameters should be declared via config/metadata so different
    # projects can reuse the same CLI engine with project-specific flags.
    parser = argparse.ArgumentParser(description="Fund load decision engine")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--input", required=True, help="Path to input NDJSON file")
    parser.add_argument("--output", help="Override output file path")
    parser.add_argument(
        "--tracing",
        choices=["enable", "disable"],
        help="Override tracing enabled flag",
    )
    parser.add_argument("--trace-path", help="Override trace JSONL file path")
    return parser


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    # Parse CLI arguments; caller passes argv for testability.
    return build_parser().parse_args(argv)


def apply_tracing_overrides(config: AppConfig, args: argparse.Namespace) -> None:
    # CLI overrides take precedence over config (Composition Root spec §6.1).
    if args.tracing is None and args.trace_path is None:
        return

    tracing = config.tracing
    if tracing is None:
        # When missing, create a minimal tracing config so overrides have a target.
        tracing = TracingConfig(
            enabled=False,
            signature=TraceSignatureConfig(),
            context_diff=TraceContextDiffConfig(),
            sink=None,
        )
        config.tracing = tracing

    if args.tracing is not None:
        tracing.enabled = args.tracing == "enable"

    if args.trace_path is not None:
        if tracing.sink is None or tracing.sink.kind != "jsonl":
            tracing.sink = TraceSinkConfig(
                kind="jsonl",
                jsonl=TraceSinkJsonlConfig(path=args.trace_path),
            )
        else:
            assert tracing.sink.jsonl is not None
            tracing.sink.jsonl.path = args.trace_path


def apply_output_override(config: AppConfig, args: argparse.Namespace) -> None:
    # Output path can be overridden by CLI (Composition Root spec §3.1).
    if args.output is not None:
        config.output.file_path = args.output


def run(argv: Sequence[str] | None = None) -> int:
    # CLI run flow follows Composition Root spec §6.1.
    # This function is meant to remain a simple orchestration wrapper; business logic lives elsewhere.
    args = parse_args(argv)
    config = load_config(Path(args.config))
    apply_tracing_overrides(config, args)
    apply_output_override(config, args)

    input_source = FileInputSource(Path(args.input))
    output_sink = FileOutputSink(Path(config.output.file_path))
    wiring = {
        "prime_checker": SievePrimeChecker.from_max(0),
        "window_store": InMemoryWindowStore(),
        "output_sink": output_sink,
    }
    runtime = build_runtime_from_app_config(config=config, wiring=wiring, run_id="cli")
    runtime.runner.run(input_source.read(), output_sink=lambda _: None)
    output_sink.close()
    return 0
