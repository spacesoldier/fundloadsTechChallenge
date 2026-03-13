from __future__ import annotations

from research_ui.debug_vitrine.postgres_store import PostgresRunStore


class PostgresRunRepository(PostgresRunStore):
    """PostgreSQL repository for debug run snapshots and events."""


__all__ = ["PostgresRunRepository"]
