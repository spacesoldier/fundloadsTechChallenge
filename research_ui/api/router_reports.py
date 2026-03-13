from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from fastapi import APIRouter

_LOG = logging.getLogger("research_ui.webapp")


def build_reports_router(
    *,
    reports_dir: Path,
    list_reports_fn: Callable[[Path], list[dict[str, object]]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/reports")
    def api_reports() -> dict[str, object]:
        _LOG.info("api_reports requested reports_dir=%s", reports_dir)
        return {
            "reports_dir": str(reports_dir),
            "reports": list_reports_fn(reports_dir),
        }

    return router
