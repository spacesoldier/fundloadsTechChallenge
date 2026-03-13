from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from research_ui.debug_vitrine_model import build_process_column_order, enrich_events_with_timeline

from .event_ops import is_communication_event
from .memory_store import InMemoryRunStore
from .source import RedisDebugSource
from .store_protocol import RunStore
from .types import RunSnapshot


@dataclass(slots=True)
class DebugVitrineService:
    redis_source: RedisDebugSource
    memory_store: InMemoryRunStore
    stores: list[RunStore]
    _snapshot_versions: dict[str, tuple[int, str | None]] = field(default_factory=dict)
    _snapshot_versions_lock: Lock = field(default_factory=Lock)

    def reload_from_redis(
        self,
        *,
        run_id: str | None = None,
        limit_runs: int = 20,
        max_events_per_process: int = 100_000,
    ) -> dict[str, object]:
        snapshots = self.redis_source.load_run_snapshots(
            run_id=run_id,
            limit_runs=limit_runs,
            max_events_per_process=max_events_per_process,
        )
        persisted: dict[str, int] = {
            "runs": 0,
            "events": 0,
            "processes": 0,
        }
        skipped_unchanged_runs = 0
        store_errors: list[str] = []
        for snapshot in snapshots:
            if not self._should_persist_snapshot(snapshot):
                skipped_unchanged_runs += 1
                continue
            self.memory_store.save(snapshot)
            persisted["runs"] += 1
            persisted["events"] += len(snapshot.events)
            persisted["processes"] += len(snapshot.processes)
            for store in self.stores:
                try:
                    store.save(snapshot)
                except Exception as exc:
                    store_errors.append(f"{type(store).__name__}: {exc}")
        return {
            "loaded_runs": persisted["runs"],
            "loaded_events": persisted["events"],
            "loaded_processes": persisted["processes"],
            "skipped_unchanged_runs": skipped_unchanged_runs,
            "store_errors": store_errors,
        }

    def _should_persist_snapshot(self, snapshot: RunSnapshot) -> bool:
        marker = (snapshot.total_records, snapshot.last_ts)
        with self._snapshot_versions_lock:
            previous = self._snapshot_versions.get(snapshot.run_id)
            if previous == marker:
                return False
            self._snapshot_versions[snapshot.run_id] = marker
            return True

    def list_runs(self, *, limit: int = 50) -> list[dict[str, object]]:
        for store in self.stores:
            try:
                runs = store.list_runs(limit=limit)
            except Exception:
                continue
            if runs:
                return runs
        return self.memory_store.list_runs(limit=limit)

    def run_events(
        self,
        *,
        run_id: str,
        execution_group_order: list[str],
    ) -> dict[str, object]:
        processes: list[dict[str, object]] = []
        events: list[dict[str, object]] = []
        for store in self.stores:
            try:
                processes, events = store.run_events(run_id)
            except Exception:
                continue
            if events:
                break
        if not events:
            processes, events = self.memory_store.run_events(run_id)
        enriched = enrich_events_with_timeline(events)
        for idx, event in enumerate(enriched):
            event["seq"] = idx
            event["is_communication"] = is_communication_event(event)
        process_ids = [str(item.get("process_id", "")) for item in processes]
        if not process_ids:
            process_ids = [str(item.get("process_id", "")) for item in enriched]
        process_ids = [item for item in process_ids if item]
        column_order = build_process_column_order(
            process_ids,
            execution_group_order=execution_group_order,
        )
        return {
            "run_id": run_id,
            "column_order": column_order,
            "processes": processes,
            "events": enriched,
            "event_count": len(enriched),
        }

    def query_events(
        self,
        *,
        run_id: str,
        execution_group_order: list[str],
        process_id: str | None = None,
        event_name: str | None = None,
        payload_model: str | None = None,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, object]:
        processes: list[dict[str, object]] = []
        events: list[dict[str, object]] = []
        total_matched = 0
        for store in self.stores:
            try:
                processes, events, total_matched = store.query_events(
                    run_id,
                    process_id=process_id,
                    event_name=event_name,
                    payload_model=payload_model,
                    timestamp_from=timestamp_from,
                    timestamp_to=timestamp_to,
                    limit=limit,
                    offset=offset,
                )
            except Exception:
                continue
            if events or total_matched > 0:
                break
        else:
            processes, events, total_matched = self.memory_store.query_events(
                run_id,
                process_id=process_id,
                event_name=event_name,
                payload_model=payload_model,
                timestamp_from=timestamp_from,
                timestamp_to=timestamp_to,
                limit=limit,
                offset=offset,
            )

        enriched = enrich_events_with_timeline(events)
        for idx, event in enumerate(enriched):
            event["seq"] = max(0, int(offset)) + idx
            event["is_communication"] = is_communication_event(event)
        process_ids = [str(item.get("process_id", "")) for item in processes]
        if not process_ids:
            process_ids = [str(item.get("process_id", "")) for item in enriched]
        process_ids = [item for item in process_ids if item]
        column_order = build_process_column_order(
            process_ids,
            execution_group_order=execution_group_order,
        )
        return {
            "run_id": run_id,
            "column_order": column_order,
            "processes": processes,
            "events": enriched,
            "event_count": len(enriched),
            "total_matched": total_matched,
            "limit": max(1, int(limit)),
            "offset": max(0, int(offset)),
            "filters": {
                "process_id": process_id,
                "event_name": event_name,
                "payload_model": payload_model,
                "timestamp_from": timestamp_from,
                "timestamp_to": timestamp_to,
            },
        }
