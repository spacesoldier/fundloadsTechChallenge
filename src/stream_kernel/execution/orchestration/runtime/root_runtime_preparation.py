from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
    ControlPlaneRootRuntimeBootstrapService,
)


def prepare_process_supervisor_root_runtime(artifacts: object) -> None:
    runtime = getattr(artifacts, "runtime", None)
    if not isinstance(runtime, dict):
        return
    process_role = runtime.get("__process_role")
    if isinstance(process_role, str) and process_role in {"worker", "observability_worker"}:
        return

    scope = getattr(artifacts, "scenario_scope", None)
    if scope is None:
        return
    try:
        service = scope.resolve("service", ControlPlaneRootRuntimeBootstrapService)
    except Exception:
        return

    prepare = getattr(service, "prepare_root_runtime", None)
    if not callable(prepare):
        return

    modules = getattr(artifacts, "modules", [])
    discovery_modules = [module.__name__ for module in modules if hasattr(module, "__name__")]
    prepare(
        runtime=runtime,
        config=getattr(artifacts, "config", {}),
        adapters=getattr(artifacts, "adapters", {}),
        run_id=getattr(artifacts, "run_id", "run"),
        scenario_id=getattr(artifacts, "scenario_id", "scenario"),
        discovery_modules=discovery_modules,
    )


__all__ = [
    "prepare_process_supervisor_root_runtime",
]
