from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from .models import EventQueryPayload, ReloadPayload

_LOG = logging.getLogger("research_ui.webapp")


def build_debug_router(
    *,
    service_getter: Callable[[], Any],
    execution_group_order_loader: Callable[[str], list[str]],
    default_cfg: str,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/debug/reload")
    def api_debug_reload(payload: ReloadPayload) -> dict[str, object]:
        _LOG.info(
            "api_debug_reload requested run_id=%s limit_runs=%s max_events_per_process=%s",
            payload.run_id,
            payload.limit_runs,
            payload.max_events_per_process,
        )
        try:
            result = service_getter().reload_from_redis(
                run_id=payload.run_id,
                limit_runs=payload.limit_runs,
                max_events_per_process=payload.max_events_per_process,
            )
        except Exception as exc:  # pragma: no cover
            _LOG.exception("api_debug_reload failed run_id=%s", payload.run_id)
            raise HTTPException(status_code=500, detail=f"debug reload failed: {exc}") from exc
        _LOG.info("api_debug_reload finished result=%s", result)
        return result

    @router.get("/api/debug/runs")
    def api_debug_runs(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, object]:
        _LOG.info("api_debug_runs requested limit=%s", limit)
        try:
            runs = service_getter().list_runs(limit=limit)
        except Exception as exc:  # pragma: no cover
            _LOG.exception("api_debug_runs failed limit=%s", limit)
            raise HTTPException(status_code=500, detail=f"debug runs load failed: {exc}") from exc
        _LOG.info("api_debug_runs finished runs=%s", len(runs))
        return {"runs": runs}

    @router.get("/api/debug/runs/{run_id}/events")
    def api_debug_run_events(
        run_id: str,
        config_path: str = Query(default=default_cfg),
    ) -> dict[str, object]:
        _LOG.info("api_debug_run_events requested run_id=%s config_path=%s", run_id, config_path)
        try:
            group_order = execution_group_order_loader(config_path)
            return service_getter().run_events(
                run_id=run_id,
                execution_group_order=group_order,
            )
        except Exception as exc:  # pragma: no cover
            _LOG.exception("api_debug_run_events failed run_id=%s", run_id)
            raise HTTPException(status_code=500, detail=f"debug events load failed: {exc}") from exc

    @router.post("/api/debug/runs/{run_id}/events/query")
    def api_debug_run_events_query(
        run_id: str,
        payload: EventQueryPayload,
        config_path: str = Query(default=default_cfg),
    ) -> dict[str, object]:
        _LOG.info(
            "api_debug_run_events_query requested run_id=%s process_id=%s event_name=%s payload_model=%s limit=%s offset=%s",
            run_id,
            payload.process_id,
            payload.event_name,
            payload.payload_model,
            payload.limit,
            payload.offset,
        )
        try:
            group_order = execution_group_order_loader(config_path)
            return service_getter().query_events(
                run_id=run_id,
                execution_group_order=group_order,
                process_id=payload.process_id,
                event_name=payload.event_name,
                payload_model=payload.payload_model,
                timestamp_from=payload.timestamp_from,
                timestamp_to=payload.timestamp_to,
                limit=payload.limit,
                offset=payload.offset,
            )
        except Exception as exc:  # pragma: no cover
            _LOG.exception("api_debug_run_events_query failed run_id=%s", run_id)
            raise HTTPException(status_code=500, detail=f"debug events query failed: {exc}") from exc

    return router
