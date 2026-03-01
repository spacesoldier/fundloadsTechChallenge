from __future__ import annotations


class RuntimeLifecycleManager:
    # Runtime lifecycle contract for execution workers/process group supervisor.
    def start(self) -> None:
        raise NotImplementedError("RuntimeLifecycleManager.start must be implemented")

    def ready(self, timeout_seconds: int) -> bool:
        raise NotImplementedError("RuntimeLifecycleManager.ready must be implemented")

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        raise NotImplementedError("RuntimeLifecycleManager.stop must be implemented")


__all__ = ["RuntimeLifecycleManager"]

