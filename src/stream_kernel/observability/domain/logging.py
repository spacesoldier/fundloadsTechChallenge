from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class LogMessage:
    # Framework-level structured log payload for stream observability pipelines.
    level: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    fields: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.level or not self.message:
            raise ValueError("LogMessage requires non-empty level/message")
