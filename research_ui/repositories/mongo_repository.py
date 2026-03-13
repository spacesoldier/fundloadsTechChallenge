from __future__ import annotations

from research_ui.debug_vitrine.mongo_store import MongoRunStore


class MongoRunRepository(MongoRunStore):
    """MongoDB repository for debug run snapshots and events."""


__all__ = ["MongoRunRepository"]
