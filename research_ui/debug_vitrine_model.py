from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_OBSERVABILITY_GROUP = "system.observability"


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    process_id: str
    role: str
    execution_group: str
    worker_index: int


def parse_process_identity(process_id: str) -> ProcessIdentity:
    parts = process_id.split(":")
    role = parts[0] if len(parts) >= 1 and parts[0] else "leaf"
    execution_group = parts[1] if len(parts) >= 2 and parts[1] else "unknown"
    worker_index = 0
    if len(parts) >= 3:
        raw = parts[2]
        if raw.startswith("w"):
            raw = raw[1:]
        try:
            worker_index = int(raw)
        except Exception:
            worker_index = 0
    return ProcessIdentity(
        process_id=process_id,
        role=role,
        execution_group=execution_group,
        worker_index=worker_index,
    )


def build_process_column_order(
    process_ids: list[str],
    *,
    execution_group_order: list[str] | tuple[str, ...] = (),
) -> list[str]:
    order_map = {name: idx for idx, name in enumerate(execution_group_order)}
    deduped = list(dict.fromkeys(pid for pid in process_ids if isinstance(pid, str) and pid))

    def _sort_key(pid: str) -> tuple[int, int, str, int, str]:
        ident = parse_process_identity(pid)
        if ident.execution_group == _OBSERVABILITY_GROUP:
            return (0, 0, ident.execution_group, ident.worker_index, ident.process_id)
        if ident.role == "root":
            return (1, 0, ident.execution_group, ident.worker_index, ident.process_id)
        group_rank = order_map.get(ident.execution_group, 10_000)
        band = 2 if group_rank < 10_000 else 3
        return (band, group_rank, ident.execution_group, ident.worker_index, ident.process_id)

    return sorted(deduped, key=_sort_key)


def sort_events_chronologically(
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    prepared: list[tuple[int, datetime, str, dict[str, object]]] = []
    for idx, event in enumerate(events):
        timestamp = _coerce_timestamp(event.get("timestamp"))
        process_id = _coerce_str(event.get("process_id"), default="unknown")
        prepared.append((idx, timestamp, process_id, event))
    prepared.sort(key=lambda item: (item[1], item[2], item[0]))
    return [item[3] for item in prepared]


def enrich_events_with_timeline(
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    sorted_events = sort_events_chronologically(events)
    if not sorted_events:
        return []
    timestamps = [_coerce_timestamp(item.get("timestamp")) for item in sorted_events]
    min_ms = min(_to_ms(ts) for ts in timestamps)
    max_ms = max(_to_ms(ts) for ts in timestamps)
    span = max(1, max_ms - min_ms)
    result: list[dict[str, object]] = []
    for event, ts in zip(sorted_events, timestamps, strict=False):
        ms = _to_ms(ts)
        ratio = float(ms - min_ms) / float(span)
        enriched = dict(event)
        enriched["timeline_ms"] = ms
        enriched["timeline_ratio"] = max(0.0, min(1.0, ratio))
        result.append(enriched)
    return result


def _coerce_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str) and value:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(raw)
        except Exception:
            ts = datetime.now(tz=UTC)
    else:
        ts = datetime.now(tz=UTC)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _coerce_str(value: object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def load_execution_group_order(config: dict[str, object]) -> list[str]:
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        return []
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    process_groups = platform.get("process_groups", [])
    if not isinstance(process_groups, list):
        return []
    result: list[str] = []
    for item in process_groups:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name and name != _OBSERVABILITY_GROUP:
            result.append(name)
    return result


__all__ = [
    "ProcessIdentity",
    "build_process_column_order",
    "enrich_events_with_timeline",
    "load_execution_group_order",
    "parse_process_identity",
    "sort_events_chronologically",
]

