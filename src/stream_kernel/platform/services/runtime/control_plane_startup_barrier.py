from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service


@runtime_checkable
class ControlPlaneStartupBarrierService(Protocol):
    def mark_discovery_completed(self, *, runtime: dict[str, object]) -> bool:
        raise NotImplementedError(
            "ControlPlaneStartupBarrierService.mark_discovery_completed must be implemented"
        )

    def mark_config_completed(self, *, runtime: dict[str, object]) -> bool:
        raise NotImplementedError(
            "ControlPlaneStartupBarrierService.mark_config_completed must be implemented"
        )

    def is_open(self) -> bool:
        raise NotImplementedError("ControlPlaneStartupBarrierService.is_open must be implemented")

    def reset(self) -> None:
        raise NotImplementedError("ControlPlaneStartupBarrierService.reset must be implemented")


@service(name="control_plane_startup_barrier_service")
@dataclass(slots=True)
class InMemoryControlPlaneStartupBarrierService(ControlPlaneStartupBarrierService):
    _discovery_completed: bool = False
    _config_completed: bool = False
    _open: bool = False

    def mark_discovery_completed(self, *, runtime: dict[str, object]) -> bool:
        _ = runtime
        self._discovery_completed = True
        return self._maybe_open()

    def mark_config_completed(self, *, runtime: dict[str, object]) -> bool:
        _ = runtime
        self._config_completed = True
        return self._maybe_open()

    def is_open(self) -> bool:
        return self._open

    def reset(self) -> None:
        self._discovery_completed = False
        self._config_completed = False
        self._open = False

    def _maybe_open(self) -> bool:
        if self._open:
            return False
        if self._discovery_completed and self._config_completed:
            self._open = True
            return True
        return False


__all__ = [
    "ControlPlaneStartupBarrierService",
    "InMemoryControlPlaneStartupBarrierService",
]
