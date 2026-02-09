from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class TelemetryMessage:
    # Framework-level metric payload for stream observability pipelines.
    metric: str
    value: int | float
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("TelemetryMessage requires non-empty metric")
