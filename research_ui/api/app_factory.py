from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .router_debug import build_debug_router
from .router_health import build_health_router
from .router_pages import build_pages_router
from .router_reports import build_reports_router
from .router_topology import build_topology_router


def create_research_ui_app(
    *,
    service_getter: Callable[[], Any],
    ingest_worker_getter: Callable[[], Any],
    execution_group_order_loader: Callable[[str], list[str]],
    topology_builder: Callable[[str], dict[str, object]],
    list_reports_fn: Callable[[Path], list[dict[str, object]]],
    default_cfg: str,
    static_dir: Path,
    reports_dir: Path,
) -> FastAPI:
    app = FastAPI(title="Stream Kernel Research UI", version="0.3.0")
    app.mount("/static", StaticFiles(directory=str(static_dir), check_dir=False), name="static")
    app.mount("/reports", StaticFiles(directory=str(reports_dir), check_dir=False), name="reports")

    @app.on_event("startup")
    def _startup() -> None:
        ingest_worker_getter().start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        ingest_worker_getter().stop()

    app.include_router(build_health_router())
    app.include_router(
        build_topology_router(
            default_cfg=default_cfg,
            topology_builder=topology_builder,
        )
    )
    app.include_router(
        build_reports_router(
            reports_dir=reports_dir,
            list_reports_fn=list_reports_fn,
        )
    )
    app.include_router(
        build_debug_router(
            service_getter=service_getter,
            execution_group_order_loader=execution_group_order_loader,
            default_cfg=default_cfg,
        )
    )
    app.include_router(build_pages_router(static_dir=static_dir))
    return app
