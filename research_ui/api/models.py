from __future__ import annotations

from pydantic import BaseModel, Field


class ReloadPayload(BaseModel):
    run_id: str | None = None
    limit_runs: int = Field(default=20, ge=1, le=500)
    max_events_per_process: int = Field(default=100000, ge=100, le=1000000)


class EventQueryPayload(BaseModel):
    process_id: str | None = None
    event_name: str | None = None
    payload_model: str | None = None
    timestamp_from: str | None = None
    timestamp_to: str | None = None
    limit: int = Field(default=500, ge=1, le=50000)
    offset: int = Field(default=0, ge=0, le=1000000)


__all__ = ["EventQueryPayload", "ReloadPayload"]
