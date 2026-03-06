from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def list_reports(reports_dir: str | Path) -> list[dict[str, object]]:
    base = Path(reports_dir)
    if not base.exists() or not base.is_dir():
        return []

    rows_with_ts: list[tuple[float, str, dict[str, object]]] = []
    for path in base.glob("*.html"):
        if not path.is_file():
            continue
        stat = path.stat()
        row = {
            "name": path.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }
        rows_with_ts.append((stat.st_mtime, path.name, row))

    rows_with_ts.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in rows_with_ts]
