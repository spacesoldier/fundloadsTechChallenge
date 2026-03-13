from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

_LOG = logging.getLogger("research_ui.webapp")


def build_pages_router(*, static_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=FileResponse)
    def topology_page() -> FileResponse:
        _LOG.info("topology page requested")
        return FileResponse(static_dir / "topology.html")

    @router.get("/debug-vitrine", response_class=FileResponse)
    def debug_vitrine_page() -> FileResponse:
        _LOG.info("debug vitrine page requested")
        return FileResponse(static_dir / "debug-vitrine.html")

    return router
