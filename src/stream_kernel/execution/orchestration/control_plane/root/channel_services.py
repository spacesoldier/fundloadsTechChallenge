from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.service import service


@runtime_checkable
class ControlPlaneRootRunnerControlService(Protocol):
    def bind_runner_stop(self, callback: object) -> None:
        raise NotImplementedError

    def clear_runner_stop(self) -> None:
        raise NotImplementedError

    def request_stop(self) -> None:
        raise NotImplementedError

    def stop_requested(self) -> bool:
        raise NotImplementedError


@service(name="control_plane_root_runner_control_service")
@dataclass(slots=True)
class DefaultControlPlaneRootRunnerControlService(ControlPlaneRootRunnerControlService):
    _request_stop_callback: object | None = None
    _stop_requested: bool = False

    def bind_runner_stop(self, callback: object) -> None:
        self._request_stop_callback = callback
        self._stop_requested = False

    def clear_runner_stop(self) -> None:
        self._request_stop_callback = None
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        callback = self._request_stop_callback
        if callable(callback):
            try:
                callback()
            except Exception:
                return

    def stop_requested(self) -> bool:
        return bool(self._stop_requested)


__all__ = [
    "ControlPlaneRootRunnerControlService",
    "DefaultControlPlaneRootRunnerControlService",
]

