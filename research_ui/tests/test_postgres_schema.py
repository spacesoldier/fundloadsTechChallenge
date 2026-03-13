from __future__ import annotations

from pathlib import Path


def test_postgres_schema_contains_payload_columns() -> None:
    path = Path("research_ui/sql/postgres_schema.sql")
    assert path.exists()
    sql = path.read_text(encoding="utf-8")
    assert "payload_model" in sql
    assert "payload_data_json" in sql
