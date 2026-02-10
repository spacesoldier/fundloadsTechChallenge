from __future__ import annotations

import argparse
import importlib
import pkgutil
from types import ModuleType
from typing import Any

from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.discovery import discover_adapters


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


def apply_cli_overrides(
    config: dict[str, object],
    args: argparse.Namespace,
    *,
    discovery_modules: list[str] | None = None,
) -> None:
    # Override adapter paths if explicitly provided via CLI flags.
    adapters = config.setdefault("adapters", {})
    if not isinstance(adapters, dict):
        raise ValueError("adapters must be a mapping")

    if args.input:
        role = _resolve_cli_adapter_role(
            config,
            adapters,
            direction="input",
            discovery_modules=discovery_modules,
        )
        _set_adapter_path(adapters, role, args.input)
    if args.output:
        role = _resolve_cli_adapter_role(
            config,
            adapters,
            direction="output",
            discovery_modules=discovery_modules,
        )
        _set_adapter_path(adapters, role, args.output)

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


def _resolve_cli_adapter_role(
    config: dict[str, object],
    adapters: dict[str, object],
    *,
    direction: str,
    discovery_modules: list[str] | None,
) -> str:
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")
    cli = runtime.get("cli", {})
    if not isinstance(cli, dict):
        raise ValueError("runtime.cli must be a mapping when provided")

    explicit_key = "input_adapter" if direction == "input" else "output_adapter"
    explicit = cli.get(explicit_key)
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit:
            raise ValueError(f"runtime.cli.{explicit_key} must be a non-empty string")
        if explicit not in adapters:
            raise ValueError(f"runtime.cli.{explicit_key} references unknown adapter: {explicit}")
        return explicit

    candidates = _discover_io_adapter_roles(
        runtime,
        adapters,
        direction=direction,
        discovery_modules=discovery_modules,
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(
            f"Cannot resolve {direction} adapter role from discovery; set runtime.cli.{explicit_key}"
        )
    raise ValueError(
        f"Multiple {direction} adapter candidates {candidates}; set runtime.cli.{explicit_key}"
    )


def _discover_io_adapter_roles(
    runtime: dict[str, object],
    adapters: dict[str, object],
    *,
    direction: str,
    discovery_modules: list[str] | None,
) -> list[str]:
    raw_modules = discovery_modules if discovery_modules is not None else runtime.get("discovery_modules", [])
    if not isinstance(raw_modules, list) or not all(isinstance(item, str) for item in raw_modules):
        return []
    modules = _load_discovery_modules(raw_modules)
    discovered = discover_adapters(modules)
    matches: list[str] = []
    for role in adapters:
        factory = discovered.get(role)
        if factory is None:
            continue
        meta = get_adapter_meta(factory)
        if meta is None:
            continue
        if direction == "input" and not meta.consumes and bool(meta.emits):
            matches.append(role)
            continue
        if direction == "output" and bool(meta.consumes) and not meta.emits:
            matches.append(role)
    return sorted(matches)


def _load_discovery_modules(discovery_modules: list[str]) -> list[ModuleType]:
    modules: list[ModuleType] = []
    seen: set[str] = set()

    def _append(module: ModuleType) -> None:
        if module.__name__ in seen:
            return
        seen.add(module.__name__)
        modules.append(module)

    for module_name in discovery_modules:
        root = importlib.import_module(module_name)
        module_path = getattr(root, "__path__", None)
        if module_path is None:
            _append(root)
            continue
        expanded = False
        for info in pkgutil.walk_packages(module_path, prefix=f"{root.__name__}."):
            expanded = True
            _append(importlib.import_module(info.name))
        if not expanded:
            _append(root)
    return modules
