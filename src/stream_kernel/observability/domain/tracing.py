from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class TraceMessage:
    # Framework-level tracing payload for kv_stream observability pipelines.
    trace_id: str
    span: str
    status: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    fields: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trace_id or not self.span or not self.status:
            raise ValueError("TraceMessage requires non-empty trace_id/span/status")
