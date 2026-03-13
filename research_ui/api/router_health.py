from __future__ import annotations

import logging

from fastapi import APIRouter

_LOG = logging.getLogger("research_ui.webapp")


def build_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        _LOG.info("health requested")
        return {"status": "ok"}

    return router
