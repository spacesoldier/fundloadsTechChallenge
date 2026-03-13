from __future__ import annotations

"""Compatibility facade for debug vitrine backend.

Implementation lives in ``research_ui.debug_vitrine`` package. Keep this module so
existing imports continue to work while code stays split into short modules.
"""

from research_ui.debug_vitrine import (
    DebugVitrineService,
    InMemoryRunStore,
    MongoRunStore,
    PostgresRunStore,
    ProcessSummary,
    RedisDebugSource,
    RunSnapshot,
    RunStore,
    build_debug_vitrine_service,
    execution_group_order_from_config,
)

_RunStore = RunStore

__all__ = [
    "DebugVitrineService",
    "InMemoryRunStore",
    "MongoRunStore",
    "PostgresRunStore",
    "ProcessSummary",
    "RedisDebugSource",
    "RunSnapshot",
    "RunStore",
    "_RunStore",
    "build_debug_vitrine_service",
    "execution_group_order_from_config",
]
