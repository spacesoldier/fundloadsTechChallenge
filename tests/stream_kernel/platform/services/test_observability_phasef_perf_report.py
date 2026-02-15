from __future__ import annotations

from pathlib import Path


def test_obs_k_f_02_perf_report_is_committed_with_required_sections() -> None:
    report = Path("docs/framework/initial_stage/_work/observability_platform_rails_phasef_perf_parity_report.md")
    assert report.exists(), "Phase F perf/parity report must be committed"
    text = report.read_text(encoding="utf-8")
    for marker in (
        "# Phase F Perf/Isolation Report",
        "## Command",
        "## Environment",
        "## Results",
        "## Notes",
    ):
        assert marker in text
