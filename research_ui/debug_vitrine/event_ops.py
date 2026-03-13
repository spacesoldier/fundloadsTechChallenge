from __future__ import annotations

import json
from datetime import UTC, datetime

from .helpers import as_optional_str, as_str, parse_iso
from .types import ProcessSummary, RunSnapshot


def build_process_summary(
    *,
    process_id: str,
    first_ts: str | None,
    last_ts: str | None,
    event_count: int,
) -> ProcessSummary:
    parts = process_id.split(":")
    role = parts[0] if len(parts) >= 1 and parts[0] else "leaf"
    group = parts[1] if len(parts) >= 2 and parts[1] else "unknown"
    worker_index = 0
    if len(parts) >= 3:
        raw = parts[2]
        if raw.startswith("w"):
            raw = raw[1:]
        try:
            worker_index = int(raw)
        except Exception:
            worker_index = 0
    return ProcessSummary(
        process_id=process_id,
        process_role=role,
        execution_group=group,
        worker_index=worker_index,
        event_count=max(0, int(event_count)),
        first_ts=first_ts,
        last_ts=last_ts,
    )


def process_summary_dict(value: ProcessSummary) -> dict[str, object]:
    return {
        "process_id": value.process_id,
        "process_role": value.process_role,
        "execution_group": value.execution_group,
        "worker_index": value.worker_index,
        "event_count": value.event_count,
        "first_ts": value.first_ts,
        "last_ts": value.last_ts,
    }


def run_summary_dict(value: RunSnapshot) -> dict[str, object]:
    return {
        "run_id": value.run_id,
        "logical_run_id": value.logical_run_id,
        "first_ts": value.first_ts,
        "last_ts": value.last_ts,
        "total_records": value.total_records,
        "processes": [process_summary_dict(item) for item in value.processes],
    }


def is_communication_event(event: dict[str, object]) -> bool:
    event_name = as_str(event.get("event")).lower()
    source = as_str(event.get("source")).lower()
    fields = event.get("fields")
    field_keys = set(fields.keys()) if isinstance(fields, dict) else set()
    if "port_type" in field_keys:
        return True
    markers = (
        "ipc",
        "handoff",
        "reply_ingress",
        "boundary",
        "send",
        "recv",
        "dispatch",
        "transport",
    )
    return any(marker in event_name or marker in source for marker in markers)


def extract_first_ts(events: list[dict[str, object]]) -> str | None:
    if not events:
        return None
    return as_optional_str(events[0].get("timestamp"))


def extract_last_ts(events: list[dict[str, object]]) -> str | None:
    if not events:
        return None
    return as_optional_str(events[-1].get("timestamp"))


def build_debug_run_event_insert(
    run_id: str, event: dict[str, object]
) -> tuple[object, ...]:
    from .helpers import as_int

    fields = event.get("fields")
    payload_model = None
    payload_data_json = None
    if isinstance(fields, dict):
        payload_model = as_optional_str(fields.get("payload_model"))
        if "payload" in fields:
            payload_data_json = json.dumps(
                fields.get("payload"), separators=(",", ":"), ensure_ascii=False
            )
    return (
        run_id,
        int(event.get("seq", 0)),
        str(event.get("process_id", "")),
        parse_iso(as_str(event.get("timestamp"))),
        as_str(event.get("event")),
        as_str(event.get("source")),
        payload_model,
        payload_data_json,
        json.dumps(event, separators=(",", ":"), ensure_ascii=False),
        json.dumps(
            fields if isinstance(fields, dict) else {},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        as_int(event.get("timeline_ms")),
    )


def filter_events(
    events: list[dict[str, object]],
    *,
    process_id: str | None = None,
    event_name: str | None = None,
    payload_model: str | None = None,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
) -> list[dict[str, object]]:
    ts_from = parse_iso(timestamp_from)
    ts_to = parse_iso(timestamp_to)
    return [
        event
        for event in events
        if event_matches(
            event,
            process_id=process_id,
            event_name=event_name,
            payload_model=payload_model,
            timestamp_from=ts_from,
            timestamp_to=ts_to,
        )
    ]


def event_matches(
    event: dict[str, object],
    *,
    process_id: str | None,
    event_name: str | None,
    payload_model: str | None,
    timestamp_from: datetime | None,
    timestamp_to: datetime | None,
) -> bool:
    if (
        isinstance(process_id, str)
        and process_id
        and as_str(event.get("process_id")) != process_id
    ):
        return False
    if (
        isinstance(event_name, str)
        and event_name
        and as_str(event.get("event")) != event_name
    ):
        return False
    if isinstance(payload_model, str) and payload_model:
        extracted = extract_payload_model(event)
        if extracted != payload_model:
            return False
    if timestamp_from is not None or timestamp_to is not None:
        ts_raw = as_optional_str(event.get("timestamp"))
        ts_value = parse_iso(ts_raw)
        if ts_value is None:
            return False
        if timestamp_from is not None and ts_value < timestamp_from:
            return False
        if timestamp_to is not None and ts_value > timestamp_to:
            return False
    return True


def extract_payload_model(event: dict[str, object]) -> str | None:
    direct = as_optional_str(event.get("payload_model"))
    if direct:
        return direct
    fields = event.get("fields")
    if isinstance(fields, dict):
        nested = as_optional_str(fields.get("payload_model"))
        if nested:
            return nested
    return None


def normalize_redis_event_record(
    *,
    parsed: object,
    run_id: str,
    process_id: str,
) -> dict[str, object] | None:
    if not isinstance(parsed, dict):
        return None
    resolved_run_id = as_optional_str(parsed.get("run_id")) or run_id
    resolved_process_id = as_optional_str(parsed.get("process_id")) or process_id
    timestamp = as_optional_str(parsed.get("timestamp")) or datetime.now(UTC).isoformat()
    source = as_str(parsed.get("source"), default="redis.debug")
    if isinstance(parsed.get("event"), str) and parsed.get("event"):
        event_name = as_str(parsed.get("event"))
        fields = parsed.get("fields")
        normalized_fields: dict[str, object] = (
            dict(fields) if isinstance(fields, dict) else {}
        )
        payload_model = as_optional_str(parsed.get("payload_model"))
        if payload_model and "payload_model" not in normalized_fields:
            normalized_fields["payload_model"] = payload_model
        if "payload" in parsed and "payload" not in normalized_fields:
            normalized_fields["payload"] = parsed.get("payload")
        return {
            "run_id": resolved_run_id,
            "process_id": resolved_process_id,
            "timestamp": timestamp,
            "event": event_name,
            "source": source,
            "fields": normalized_fields,
        }
    return normalize_redis_log_record(
        parsed=parsed,
        run_id=resolved_run_id,
        process_id=resolved_process_id,
        timestamp=timestamp,
        source=source,
    )


def normalize_redis_log_record(
    *,
    parsed: dict[str, object],
    run_id: str,
    process_id: str,
    timestamp: str,
    source: str,
) -> dict[str, object] | None:
    message = as_optional_str(parsed.get("message"))
    level = as_optional_str(parsed.get("level")) or "info"
    if not message and not as_optional_str(parsed.get("event")):
        return None
    fields = parsed.get("fields")
    normalized_fields: dict[str, object] = dict(fields) if isinstance(fields, dict) else {}
    normalized_fields.setdefault("message", message or "")
    normalized_fields.setdefault("level", level)
    payload_model = as_optional_str(parsed.get("payload_model"))
    if payload_model:
        normalized_fields.setdefault("payload_model", payload_model)
    if "payload" in parsed:
        normalized_fields.setdefault("payload", parsed.get("payload"))
    event_name = as_optional_str(parsed.get("event")) or "debug.log"
    return {
        "run_id": run_id,
        "process_id": process_id,
        "timestamp": timestamp,
        "event": event_name,
        "source": source,
        "fields": normalized_fields,
    }
