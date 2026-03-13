from __future__ import annotations

from research_ui.debug_vitrine.memory_store import InMemoryRunStore


class InMemoryRunRepository(InMemoryRunStore):
    """In-memory run repository used as fallback cache."""


__all__ = ["InMemoryRunRepository"]
