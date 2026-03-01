from __future__ import annotations

from stream_kernel.application_context.service import service

from .contracts import RuntimeLifecycleManager


@service(name="runtime_lifecycle_local")
class LocalRuntimeLifecycleManager(RuntimeLifecycleManager):
    # In-process lifecycle baseline: always ready and no-op shutdown.
    def start(self) -> None:
        return None

    def ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        return True

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = graceful_timeout_seconds
        _ = drain_inflight
        return None


__all__ = ["LocalRuntimeLifecycleManager"]

