from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from research_ui.api import create_research_ui_app
from research_ui.debug_vitrine_backend import (
    build_debug_vitrine_service,
    execution_group_order_from_config,
)
from research_ui.ingest.worker import RedisDebugIngestWorker
from research_ui.reports_catalog import list_reports
from research_ui.topology_snapshot import build_topology_snapshot

_DEFAULT_CFG = "src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml"
_ROOT_DIR = Path(__file__).resolve().parent
_STATIC_DIR = (_ROOT_DIR / "static").resolve()
_REPORTS_DIR = Path(os.getenv("RESEARCH_UI_REPORTS_DIR", "research_ui/reports")).resolve()
_LOG_LEVEL = os.getenv("RESEARCH_UI_LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
_LOG = logging.getLogger("research_ui.webapp")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if isinstance(raw, str) and raw:
        try:
            return int(raw)
        except Exception:
            return default
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if isinstance(raw, str) and raw:
        try:
            return float(raw)
        except Exception:
            return default
    return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if not isinstance(raw, str) or not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_vitrine_service = build_debug_vitrine_service()
_auto_ingest_worker = RedisDebugIngestWorker(
    service=_vitrine_service,
    enabled=_env_bool("RESEARCH_UI_REDIS_AUTO_INGEST_ENABLED", True),
    interval_seconds=_env_float("RESEARCH_UI_REDIS_AUTO_INGEST_INTERVAL_SECONDS", 1.0),
    limit_runs=_env_int("RESEARCH_UI_REDIS_AUTO_INGEST_LIMIT_RUNS", 50),
    max_events_per_process=_env_int(
        "RESEARCH_UI_REDIS_AUTO_INGEST_MAX_EVENTS_PER_PROCESS",
        200_000,
    ),
)


def _service_getter():
    return _vitrine_service


def _worker_getter():
    return _auto_ingest_worker


app = create_research_ui_app(
    service_getter=_service_getter,
    ingest_worker_getter=_worker_getter,
    execution_group_order_loader=execution_group_order_from_config,
    topology_builder=build_topology_snapshot,
    list_reports_fn=list_reports,
    default_cfg=_DEFAULT_CFG,
    static_dir=_STATIC_DIR,
    reports_dir=_REPORTS_DIR,
)

_LOG.info(
    "research_ui started log_level=%s static_dir=%s reports_dir=%s",
    _LOG_LEVEL,
    _STATIC_DIR,
    _REPORTS_DIR,
)
