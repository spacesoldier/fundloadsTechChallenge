from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
)


@dataclass(frozen=True, slots=True)
class ControlPlaneRootShutdownResult:
    target_group: str
    worker_id: str
    command_id: str
    stop_ack: ControlPlaneLeafStopAckEvent | None
    stop_command_timed_out: bool
    fallback_used: bool
    worker_stopped: bool


__all__ = ["ControlPlaneRootShutdownResult"]
