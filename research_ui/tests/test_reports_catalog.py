from __future__ import annotations

import os
from pathlib import Path

from research_ui.reports_catalog import list_reports


def test_list_reports_returns_latest_first(tmp_path: Path) -> None:
    older = tmp_path / "older.html"
    newer = tmp_path / "newer.html"
    older.write_text("<html>old</html>", encoding="utf-8")
    newer.write_text("<html>new</html>", encoding="utf-8")

    older_ts = 1_700_000_000
    newer_ts = 1_700_000_100
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))

    rows = list_reports(tmp_path)

    assert [row["name"] for row in rows] == ["newer.html", "older.html"]
    assert all(isinstance(row["size_bytes"], int) for row in rows)
    assert all(isinstance(row["modified_at"], str) for row in rows)


def test_list_reports_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    rows = list_reports(tmp_path / "missing")
    assert rows == []
