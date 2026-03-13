from __future__ import annotations

import os
from datetime import UTC, datetime


def as_str(value: object, *, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def as_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def as_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(value)
        except Exception:
            return default
    return default


def parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return as_optional_str(value)


def coerce_str_value(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(value, str):
        return value
    return None


def pairs_to_dict(value: object) -> dict[str, str]:
    if not isinstance(value, list) or len(value) % 2 != 0:
        return {}
    result: dict[str, str] = {}
    for idx in range(0, len(value), 2):
        key = coerce_str_value(value[idx])
        item = coerce_str_value(value[idx + 1])
        if key is None or item is None:
            continue
        result[key] = item
    return result


def extract_stream_payload(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, list) or len(value) != 2:
        return (None, None)
    stream_id = coerce_str_value(value[0])
    raw_fields = value[1]
    payload: str | None = None
    if isinstance(raw_fields, list):
        mapped = pairs_to_dict(raw_fields)
        payload = mapped.get("payload")
        if payload is None and mapped:
            payload = next(iter(mapped.values()))
    return (stream_id, payload)


def redis_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if isinstance(raw, str) and raw:
        try:
            return int(raw)
        except Exception:
            return default
    return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if isinstance(raw, str) and raw:
        try:
            return float(raw)
        except Exception:
            return default
    return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if not isinstance(raw, str) or not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_optional(name: str) -> str | None:
    raw = os.getenv(name)
    if isinstance(raw, str) and raw:
        return raw
    return None
