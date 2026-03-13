from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, HTTPException, Query

_LOG = logging.getLogger("research_ui.webapp")


def build_topology_router(
    *,
    default_cfg: str,
    topology_builder: Callable[[str], dict[str, object]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/topology")
    def api_topology(
        config_path: str = Query(
            default=default_cfg,
            description="Path to validated newgen config file",
        ),
    ) -> dict[str, object]:
        _LOG.info("api_topology requested config_path=%s", config_path)
        try:
            return topology_builder(config_path)
        except Exception as exc:  # pragma: no cover
            _LOG.exception("api_topology failed config_path=%s", config_path)
            raise HTTPException(status_code=400, detail=f"topology build failed: {exc}") from exc

    return router
