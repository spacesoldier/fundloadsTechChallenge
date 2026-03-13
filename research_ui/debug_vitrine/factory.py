from __future__ import annotations

import os

from research_ui.debug_vitrine_model import load_execution_group_order
from research_ui.ingest.redis_stream_source import RedisStreamDebugSource
from research_ui.repositories import (
    InMemoryRunRepository,
    MongoRunRepository,
    PostgresRunRepository,
    RunStore,
)
from stream_kernel.config.loader import load_yaml_config

from .helpers import env_bool, env_float, env_int, env_optional
from .service import DebugVitrineService


def build_debug_vitrine_service() -> DebugVitrineService:
    redis_source = RedisStreamDebugSource(
        host=os.getenv("RESEARCH_UI_REDIS_HOST", "redis"),
        port=env_int("RESEARCH_UI_REDIS_PORT", 6379),
        db=env_int("RESEARCH_UI_REDIS_DB", 0),
        password=env_optional("RESEARCH_UI_REDIS_PASSWORD"),
        key_prefix=os.getenv("RESEARCH_UI_REDIS_KEY_PREFIX", "stream_kernel:debug"),
        connect_timeout_seconds=env_float(
            "RESEARCH_UI_REDIS_CONNECT_TIMEOUT_SECONDS", 0.2
        ),
        socket_timeout_seconds=env_float(
            "RESEARCH_UI_REDIS_SOCKET_TIMEOUT_SECONDS", 2.0
        ),
    )
    stores: list[RunStore] = []
    if env_bool("RESEARCH_UI_POSTGRES_ENABLED", True):
        dsn = os.getenv(
            "RESEARCH_UI_POSTGRES_DSN",
            "postgresql://postgres:postgres@127.0.0.1:5432/research_ui",
        )
        try:
            stores.append(PostgresRunRepository(dsn=dsn))
        except Exception:
            pass
    if env_bool("RESEARCH_UI_MONGO_ENABLED", True):
        uri = os.getenv("RESEARCH_UI_MONGO_URI", "mongodb://127.0.0.1:27017")
        db_name = os.getenv("RESEARCH_UI_MONGO_DB", "research_ui")
        try:
            stores.append(MongoRunRepository(uri=uri, db_name=db_name))
        except Exception:
            pass
    return DebugVitrineService(
        redis_source=redis_source,
        memory_store=InMemoryRunRepository(),
        stores=stores,
    )


def execution_group_order_from_config(config_path: str) -> list[str]:
    try:
        loaded = load_yaml_config(config_path)
    except Exception:
        return []
    if not isinstance(loaded, dict):
        return []
    return load_execution_group_order(loaded)
