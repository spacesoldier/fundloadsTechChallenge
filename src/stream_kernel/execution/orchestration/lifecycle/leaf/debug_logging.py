from __future__ import annotations

import json
from threading import Lock
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_STATE: dict[str, object] = {
    "enabled": False,
    "group_name": None,
    "worker_id": None,
    "path": None,
    "write_to_file": True,
    "buffer": [],
    "dropped": 0,
    "max_records": 200000,
}
_STATE_LOCK = Lock()


def configure_leaf_debug_logging(
    *,
    runtime: dict[str, object] | None,
    group_name: str,
    worker_id: str,
) -> Path | None:
    debug_cfg = _extract_debug_cfg(runtime)
    enabled = _is_enabled(debug_cfg)
    if not enabled:
        with _STATE_LOCK:
            current_enabled = bool(_STATE.get("enabled", False))
            current_group = _STATE.get("group_name")
            current_worker = _STATE.get("worker_id")
            current_path = _STATE.get("path")
            if (
                current_enabled
                and current_group == group_name
                and current_worker == worker_id
                and isinstance(current_path, str)
                and current_path
            ):
                return Path(current_path)
            _STATE["enabled"] = False
            _STATE["buffer"] = []
            _STATE["dropped"] = 0
            _STATE["group_name"] = group_name
            _STATE["worker_id"] = worker_id
            _STATE["path"] = None
            _STATE["write_to_file"] = True
        return None
    write_to_file = debug_cfg.get("leaf_debug_write_to_file", True)
    if not isinstance(write_to_file, bool):
        write_to_file = True
    path: Path | None = None
    if write_to_file:
        logs_dir = debug_cfg.get("leaf_debug_logs_dir")
        if not isinstance(logs_dir, str) or not logs_dir:
            logs_dir = "logs/leaf_debug"
        path = Path(logs_dir) / _leaf_debug_file_name(group_name=group_name, worker_id=worker_id)
        path.parent.mkdir(parents=True, exist_ok=True)
    max_records = debug_cfg.get("leaf_debug_max_records", 200000)
    max_records_int = 200000
    if isinstance(max_records, int) and max_records > 0:
        max_records_int = int(max_records)
    with _STATE_LOCK:
        _STATE["enabled"] = True
        _STATE["group_name"] = group_name
        _STATE["worker_id"] = worker_id
        _STATE["path"] = str(path) if isinstance(path, Path) else None
        _STATE["write_to_file"] = bool(write_to_file)
        _STATE["buffer"] = []
        _STATE["dropped"] = 0
        _STATE["max_records"] = max_records_int
    leaf_debug_log(
        event="leaf.debug_logger.configured",
        path=str(path) if isinstance(path, Path) else None,
        root_verbose_logging=bool(debug_cfg.get("root_verbose_logging", False)),
        write_mode="buffered",
        write_to_file=bool(write_to_file),
    )
    return path


def leaf_debug_log(
    *,
    event: str,
    **fields: object,
) -> None:
    if not bool(_STATE.get("enabled", False)):
        return
    payload: dict[str, object] = {
        "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "event": event,
        "group_name": _state_str("group_name"),
        "worker_id": _state_str("worker_id"),
    }
    payload.update({key: _json_safe(value) for key, value in fields.items()})
    line: str
    try:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return
    with _STATE_LOCK:
        enabled = bool(_STATE.get("enabled", False))
        if not enabled:
            return
        buffer = _STATE.get("buffer")
        if not isinstance(buffer, list):
            buffer = []
            _STATE["buffer"] = buffer
        max_records = _STATE.get("max_records", 200000)
        max_records_int = int(max_records) if isinstance(max_records, int) and max_records > 0 else 200000
        if len(buffer) >= max_records_int:
            dropped = _STATE.get("dropped", 0)
            _STATE["dropped"] = int(dropped) + 1 if isinstance(dropped, int) else 1
            return
        buffer.append(line)


def flush_leaf_debug_logging() -> Path | None:
    path_str = _state_str("path")
    with _STATE_LOCK:
        enabled = bool(_STATE.get("enabled", False))
        if not enabled:
            return Path(path_str) if isinstance(path_str, str) and path_str else None
        raw_buffer = _STATE.get("buffer")
        buffer = list(raw_buffer) if isinstance(raw_buffer, list) else []
        dropped = _STATE.get("dropped", 0)
        dropped_int = int(dropped) if isinstance(dropped, int) and dropped > 0 else 0
        write_to_file = bool(_STATE.get("write_to_file", True))
        _STATE["buffer"] = []
        _STATE["dropped"] = 0
    if dropped_int > 0:
        dropped_payload = {
            "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "event": "leaf.debug_logger.buffer_overflow",
            "group_name": _state_str("group_name"),
            "worker_id": _state_str("worker_id"),
            "dropped_records": dropped_int,
        }
        try:
            buffer.append(json.dumps(dropped_payload, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            pass
    if not write_to_file:
        return Path(path_str) if isinstance(path_str, str) and path_str else None
    if not path_str:
        return None
    if not buffer:
        return Path(path_str)
    path = Path(path_str)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(buffer))
            handle.write("\n")
    except Exception:
        return path
    return path


def _extract_debug_cfg(runtime: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(runtime, dict):
        return {}
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    debug = platform.get("debug", {})
    return debug if isinstance(debug, dict) else {}


def _is_enabled(debug_cfg: dict[str, object]) -> bool:
    explicit_enabled = debug_cfg.get("leaf_debug_enabled")
    if isinstance(explicit_enabled, bool):
        return explicit_enabled
    leaf_verbose = debug_cfg.get("leaf_verbose_logging")
    return bool(leaf_verbose) if isinstance(leaf_verbose, bool) else False


def _leaf_debug_file_name(*, group_name: str, worker_id: str) -> str:
    worker_suffix = worker_id
    if "#" in worker_id:
        candidate = worker_id.split("#")[-1]
        if candidate:
            worker_suffix = candidate
    file_name = f"{group_name}#{worker_suffix}.debug.log"
    return "".join(ch if ch.isalnum() or ch in {"#", ".", "-", "_"} else "_" for ch in file_name)


def _state_str(key: str) -> str | None:
    value = _STATE.get(key)
    return value if isinstance(value, str) and value else None


def _json_safe(value: Any) -> object:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    try:
        return repr(value)
    except Exception:
        return f"<unrepr:{type(value).__name__}>"


__all__ = [
    "configure_leaf_debug_logging",
    "flush_leaf_debug_logging",
    "leaf_debug_log",
]
