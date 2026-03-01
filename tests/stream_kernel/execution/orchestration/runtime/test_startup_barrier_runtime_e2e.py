from __future__ import annotations

import sys
from pathlib import Path

import pytest

import stream_kernel.execution.orchestration.builder as builder_module
from stream_kernel.execution.orchestration.source_ingress import SourceBootstrapNode
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    InMemoryControlPlaneStartupBarrierService,
)


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_process_supervisor_runtime_executes_deferred_source_only_after_startup_barrier_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pkg = tmp_path / "runtime_barrier_pkg"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "adapters.py",
        "\n".join(
            [
                "from stream_kernel.adapters.contracts import adapter",
                "",
                "class _Source:",
                "    def read(self):",
                "        yield {'value': 1}",
                "",
                "@adapter(name='test_source', kind='test.source', consumes=[], emits=[dict], execution_mode='async')",
                "def test_source(settings):",
                "    _ = settings",
                "    return _Source()",
            ]
        ),
    )

    events: list[str] = []
    orig_is_open = InMemoryControlPlaneStartupBarrierService.is_open
    opened_once = {"value": False}

    def _is_open(self: InMemoryControlPlaneStartupBarrierService) -> bool:
        result = orig_is_open(self)
        if result and not opened_once["value"]:
            opened_once["value"] = True
            events.append("barrier_open")
        return result

    orig_source_call = SourceBootstrapNode.__call__

    def _source_call(self: SourceBootstrapNode, msg: object, ctx: object | None) -> list[object]:
        events.append("source_call")
        return orig_source_call(self, msg, ctx)

    monkeypatch.setattr(InMemoryControlPlaneStartupBarrierService, "is_open", _is_open)
    monkeypatch.setattr(SourceBootstrapNode, "__call__", _source_call)

    config = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "strict": True,
            "discovery_modules": ["runtime_barrier_pkg.adapters"],
            "platform": {
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [],
                "runner_loop": {
                    "poll_timeout_ms": 1,
                    "idle_timeout_ms": 10,
                    "startup_barrier_timeout_ms": 200,
                },
            },
        },
        "nodes": {},
        "adapters": {
            "test_source": {"settings": {}},
        },
    }

    sys.path.insert(0, str(tmp_path))
    try:
        artifacts = builder_module.build_runtime_artifacts(config)
        builder_module.execute_runtime_artifacts(artifacts)
    finally:
        sys.path.remove(str(tmp_path))

    assert "barrier_open" in events
    assert "source_call" in events
    assert events.index("barrier_open") < events.index("source_call")


def test_process_supervisor_runtime_raises_when_startup_barrier_never_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pkg = tmp_path / "runtime_barrier_timeout_pkg"
    _write_file(pkg / "__init__.py", "")
    _write_file(
        pkg / "adapters.py",
        "\n".join(
            [
                "from stream_kernel.adapters.contracts import adapter",
                "",
                "class _Source:",
                "    def read(self):",
                "        yield {'value': 1}",
                "",
                "@adapter(name='test_source_timeout', kind='test.source.timeout', consumes=[], emits=[dict], execution_mode='async')",
                "def test_source_timeout(settings):",
                "    _ = settings",
                "    return _Source()",
            ]
        ),
    )

    monkeypatch.setattr(InMemoryControlPlaneStartupBarrierService, "is_open", lambda self: False)

    config = {
        "version": 1,
        "scenario": {"name": "baseline"},
        "runtime": {
            "strict": True,
            "discovery_modules": ["runtime_barrier_timeout_pkg.adapters"],
            "platform": {
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [],
                "runner_loop": {
                    "poll_timeout_ms": 1,
                    "idle_timeout_ms": 1,
                    "startup_barrier_timeout_ms": 5,
                },
            },
        },
        "nodes": {},
        "adapters": {
            "test_source_timeout": {"settings": {}},
        },
    }

    sys.path.insert(0, str(tmp_path))
    try:
        artifacts = builder_module.build_runtime_artifacts(config)
        with pytest.raises(RuntimeError, match="startup barrier"):
            builder_module.execute_runtime_artifacts(artifacts)
    finally:
        sys.path.remove(str(tmp_path))
