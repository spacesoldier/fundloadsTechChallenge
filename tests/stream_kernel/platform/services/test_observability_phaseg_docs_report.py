from __future__ import annotations

from pathlib import Path


def test_obs_k_g_01_docs_closure_report_is_committed_with_required_sections() -> None:
    report = Path(
        "docs/framework/initial_stage/_work/observability_platform_rails_phaseg_docs_closure_report.md"
    )
    assert report.exists(), "Phase G docs-closure report must be committed"
    text = report.read_text(encoding="utf-8")
    for marker in (
        "# Phase G Docs/Migration Closure Report",
        "## Scope",
        "## Updated docs",
        "## Migration closure checks",
        "## Notes",
    ):
        assert marker in text


def test_obs_k_g_02_tracing_doc_marks_legacy_path_and_removes_factory_wording() -> None:
    tracing_doc = Path("docs/framework/initial_stage/Tracing runtime.md")
    text = tracing_doc.read_text(encoding="utf-8")
    assert "runtime.observability.pipeline" in text
    assert "Legacy compatibility (`runtime.tracing`)" in text
    assert "sink adapter factory" not in text
    assert "src/stream_kernel/execution/builder.py" not in text
    assert "src/stream_kernel/execution/runner.py" not in text
