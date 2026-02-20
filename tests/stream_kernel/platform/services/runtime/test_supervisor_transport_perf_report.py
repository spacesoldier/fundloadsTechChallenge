from __future__ import annotations

from pathlib import Path


def test_supervisor_transport_phase_f_perf_report_is_committed_with_required_sections() -> None:
    report = Path(
        "docs/framework/initial_stage/_work/"
        "supervisor_transport_only_phasef_regression_perf_report.md"
    )
    assert report.exists(), "Phase F performance report must be committed"
    text = report.read_text(encoding="utf-8")
    for marker in (
        "# Phase F Supervisor Transport-Only Regression/Perf Report",
        "## Command",
        "## Environment",
        "## Results",
        "## Migration Notes",
        "## Operator Runbook",
    ):
        assert marker in text
