from __future__ import annotations

from pathlib import Path


def test_api_reg_03_perf_report_is_committed_with_required_sections() -> None:
    report = Path("docs/framework/initial_stage/_work/platform_api_phaseG_regression_perf_report.md")
    assert report.exists(), "Phase G performance report must be committed"
    text = report.read_text(encoding="utf-8")
    for marker in (
        "# Phase G Regression/Perf Report",
        "## Command",
        "## Environment",
        "## Results",
        "## Notes",
    ):
        assert marker in text
