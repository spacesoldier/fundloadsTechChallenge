from __future__ import annotations

from pathlib import Path

from stream_kernel.doctor import check_module


def test_check_module_reports_node_service_adapter_targets() -> None:
    report = check_module("src/stream_kernel/adapters/file_io.py")
    assert report.ok is True
    assert report.adapter_decorated_targets >= 1


def test_check_module_detects_node_class_without_call(tmp_path: Path) -> None:
    module_path = tmp_path / "bad_node_module.py"
    module_path.write_text(
        "\n".join(
            [
                "from stream_kernel.kernel.node import node",
                "",
                "@node(name='bad.node', consumes=[], emits=[])",
                "class BadNode:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    report = check_module(str(module_path))
    assert report.ok is False
    assert any("__call__" in violation for violation in report.violations)

