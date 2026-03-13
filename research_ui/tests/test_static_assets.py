from __future__ import annotations

from pathlib import Path


def test_debug_vitrine_static_page_exists() -> None:
    path = Path("research_ui/static/debug-vitrine.html")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "/api/debug/reload" in content
    assert "/api/debug/runs" in content
    assert "/events/query" in content
