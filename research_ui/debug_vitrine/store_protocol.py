from __future__ import annotations

from typing import Protocol

from .types import RunSnapshot


class RunStore(Protocol):
    def save(self, snapshot: RunSnapshot) -> None:
        raise NotImplementedError

    def list_runs(self, *, limit: int = 50) -> list[dict[str, object]]:
        raise NotImplementedError

    def run_events(
        self, run_id: str
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        raise NotImplementedError

    def query_events(
        self,
        run_id: str,
        *,
        process_id: str | None = None,
        event_name: str | None = None,
        payload_model: str | None = None,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
        raise NotImplementedError
