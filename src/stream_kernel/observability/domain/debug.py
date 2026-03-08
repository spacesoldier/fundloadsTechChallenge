from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class DebugMessage:
    # Structured runtime debug record routed through dedicated debug pipeline.
    timestamp: datetime
    event: str
    source: str
    fields: dict[str, object] = field(default_factory=dict)
    run_id: str | None = None
    run_instance_id: str | None = None
    process_group: str | None = None
    worker_id: str | None = None
    trace_id: str | None = None


def debug_message_now(
    *,
    event: str,
    source: str,
    fields: dict[str, object] | None = None,
    run_id: str | None = None,
    run_instance_id: str | None = None,
    process_group: str | None = None,
    worker_id: str | None = None,
    trace_id: str | None = None,
) -> DebugMessage:
    return DebugMessage(
        timestamp=datetime.now(tz=UTC),
        event=event,
        source=source,
        fields=dict(fields or {}),
        run_id=run_id,
        run_instance_id=run_instance_id,
        process_group=process_group,
        worker_id=worker_id,
        trace_id=trace_id,
    )


__all__ = ["DebugMessage", "debug_message_now"]
