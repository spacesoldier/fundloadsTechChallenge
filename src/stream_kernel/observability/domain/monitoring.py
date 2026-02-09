from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class MonitoringMessage:
    # Framework-level monitoring payload for health/state event streams.
    name: str
    status: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.status:
            raise ValueError("MonitoringMessage requires non-empty name/status")
