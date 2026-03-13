from __future__ import annotations

from stream_kernel.execution.orchestration.lifecycle import runtime_bootstrap_mode


def is_process_supervisor_mode(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    try:
        return runtime_bootstrap_mode(runtime) == "process_supervisor"
    except Exception:
        return False


def is_worker_role(runtime: dict[str, object] | None) -> bool:
    if not isinstance(runtime, dict):
        return False
    process_role = runtime.get("__process_role")
    return isinstance(process_role, str) and process_role in {"worker", "observability_worker"}


def is_root_process_supervisor_runtime(runtime: dict[str, object] | None) -> bool:
    return is_process_supervisor_mode(runtime) and not is_worker_role(runtime)


def should_include_business_steps(runtime: dict[str, object] | None) -> bool:
    return not is_root_process_supervisor_runtime(runtime)

