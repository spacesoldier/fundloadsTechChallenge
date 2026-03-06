from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER_NAME = "stream_kernel.leaf.debug"
_STATE: dict[str, object] = {
    "enabled": False,
    "group_name": None,
    "worker_id": None,
    "path": None,
}


def configure_leaf_debug_logging(
    *,
    runtime: dict[str, object] | None,
    group_name: str,
    worker_id: str,
) -> Path | None:
    debug_cfg = _extract_debug_cfg(runtime)
    enabled = _is_enabled(debug_cfg)
    if not enabled:
        _STATE["enabled"] = False
        return None
    logs_dir = debug_cfg.get("leaf_debug_logs_dir")
    if not isinstance(logs_dir, str) or not logs_dir:
        logs_dir = "logs/leaf_debug"
    path = Path(logs_dir) / _leaf_debug_file_name(group_name=group_name, worker_id=worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    current_path = str(path.resolve())
    existing = next(
        (
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.FileHandler)
            and str(Path(handler.baseFilename).resolve()) == current_path
        ),
        None,
    )
    if existing is None:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(file_handler)
    _STATE["enabled"] = True
    _STATE["group_name"] = group_name
    _STATE["worker_id"] = worker_id
    _STATE["path"] = str(path)
    leaf_debug_log(
        event="leaf.debug_logger.configured",
        path=str(path),
        root_verbose_logging=bool(debug_cfg.get("root_verbose_logging", False)),
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
    try:
        logging.getLogger(_LOGGER_NAME).debug(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
    except Exception:
        return


def _extract_debug_cfg(runtime: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(runtime, dict):
        return {}
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    debug = platform.get("debug", {})
    return debug if isinstance(debug, dict) else {}


def _is_enabled(debug_cfg: dict[str, object]) -> bool:
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
    "leaf_debug_log",
]
