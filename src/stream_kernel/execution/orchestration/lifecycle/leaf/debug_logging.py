from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.observability.domain.debug import DebugMessage, debug_message_now
from stream_kernel.platform.services.runtime.debug_buffer import RuntimeDebugBufferService


@runtime_checkable
class LeafLifecycleDebugStore(Protocol):
    def reset(self) -> None:
        raise NotImplementedError

    def configure_limit(self, *, max_records: int) -> None:
        raise NotImplementedError

    def enqueue_message(self, message: DebugMessage) -> None:
        raise NotImplementedError

    def drain_messages(self) -> list[DebugMessage]:
        raise NotImplementedError

    def enqueue_line(self, line: str) -> None:
        raise NotImplementedError

    def drain_lines(self) -> list[str]:
        raise NotImplementedError

    def consume_dropped(self) -> int:
        raise NotImplementedError


@service(name="leaf_lifecycle_debug_store")
@dataclass(slots=True)
class InMemoryLeafLifecycleDebugStore(LeafLifecycleDebugStore):
    _messages: deque[DebugMessage] = field(default_factory=deque)
    _lines: deque[str] = field(default_factory=deque)
    _max_records: int = 200000
    _dropped: int = 0

    def reset(self) -> None:
        self._messages.clear()
        self._lines.clear()
        self._dropped = 0

    def configure_limit(self, *, max_records: int) -> None:
        self._max_records = max(1, int(max_records))

    def enqueue_message(self, message: DebugMessage) -> None:
        if len(self._messages) >= self._max_records:
            self._dropped += 1
            return
        self._messages.append(message)

    def drain_messages(self) -> list[DebugMessage]:
        drained: list[DebugMessage] = []
        while self._messages:
            drained.append(self._messages.popleft())
        return drained

    def enqueue_line(self, line: str) -> None:
        if not line:
            return
        if len(self._lines) >= self._max_records:
            self._dropped += 1
            return
        self._lines.append(line)

    def drain_lines(self) -> list[str]:
        drained: list[str] = []
        while self._lines:
            drained.append(self._lines.popleft())
        return drained

    def consume_dropped(self) -> int:
        dropped = self._dropped
        self._dropped = 0
        return dropped


@runtime_checkable
class LeafLifecycleDebugLoggingService(Protocol):
    def configure(self, *, runtime: dict[str, object] | None, group_name: str, worker_id: str) -> Path | None:
        raise NotImplementedError

    def bind_sink(self, sink: object | None) -> None:
        raise NotImplementedError

    def log(self, *, event: str, **fields: object) -> None:
        raise NotImplementedError

    def flush(self) -> Path | None:
        raise NotImplementedError


@service(name="leaf_lifecycle_debug_logging_service")
@dataclass(slots=True)
class DefaultLeafLifecycleDebugLoggingService(LeafLifecycleDebugLoggingService):
    store: LeafLifecycleDebugStore = inject.service(LeafLifecycleDebugStore)
    runtime_debug_buffer: RuntimeDebugBufferService | None = inject.service(RuntimeDebugBufferService)
    enabled: bool = False
    group_name: str | None = None
    worker_id: str | None = None
    path: Path | None = None
    write_to_file: bool = True
    _sink_override: object | None = None

    def configure(self, *, runtime: dict[str, object] | None, group_name: str, worker_id: str) -> Path | None:
        debug_cfg = _extract_debug_cfg(runtime)
        if not _is_enabled(debug_cfg):
            self.enabled = False
            self.group_name = group_name
            self.worker_id = worker_id
            self.path = None
            self.write_to_file = True
            self._sink_override = None
            self.store.configure_limit(max_records=200000)
            self.store.reset()
            return None
        write_to_file = debug_cfg.get("leaf_debug_write_to_file", True)
        if not isinstance(write_to_file, bool):
            write_to_file = True
        max_records = debug_cfg.get("leaf_debug_max_records", 200000)
        max_records_int = int(max_records) if isinstance(max_records, int) and max_records > 0 else 200000
        self.enabled = True
        self.group_name = group_name
        self.worker_id = worker_id
        self.write_to_file = bool(write_to_file)
        self.path = _resolve_debug_path(
            debug_cfg=debug_cfg,
            group_name=group_name,
            worker_id=worker_id,
            write_to_file=bool(write_to_file),
        )
        self.store.configure_limit(max_records=max_records_int)
        self.store.reset()
        self.log(
            event="leaf.debug_logger.configured",
            path=str(self.path) if isinstance(self.path, Path) else None,
            root_verbose_logging=bool(debug_cfg.get("root_verbose_logging", False)),
            write_mode="runtime_debug_buffer",
            write_to_file=bool(write_to_file),
        )
        return self.path

    def bind_sink(self, sink: object | None) -> None:
        self._sink_override = sink if callable(getattr(sink, "publish", None)) else None
        self._flush_messages()

    def log(self, *, event: str, **fields: object) -> None:
        if not self.enabled:
            return
        safe_fields = {key: _json_safe(value) for key, value in fields.items()}
        message = debug_message_now(
            event=event,
            source="stream_kernel.execution.orchestration.lifecycle.leaf",
            fields=safe_fields,
            run_id=_env("STREAM_KERNEL_LOGICAL_RUN_ID"),
            run_instance_id=_env("STREAM_KERNEL_RUN_INSTANCE_ID"),
            process_group=self.group_name or _env("STREAM_KERNEL_PROCESS_GROUP"),
            worker_id=self.worker_id or _env("STREAM_KERNEL_WORKER_ID"),
            trace_id=None,
        )
        if self._emit_to_sink(message):
            self._flush_messages()
        else:
            self.store.enqueue_message(message)
        if self.write_to_file:
            line = _serialize_line(
                event=event,
                fields=safe_fields,
                group_name=self.group_name,
                worker_id=self.worker_id,
            )
            if isinstance(line, str) and line:
                self.store.enqueue_line(line)

    def flush(self) -> Path | None:
        self._flush_messages()
        dropped = self.store.consume_dropped()
        if dropped > 0:
            overflow = {
                "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                "event": "leaf.debug_logger.buffer_overflow",
                "group_name": self.group_name,
                "worker_id": self.worker_id,
                "dropped_records": int(dropped),
            }
            try:
                self.store.enqueue_line(json.dumps(overflow, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                pass
        if not self.write_to_file:
            return self.path
        if not isinstance(self.path, Path):
            return None
        lines = self.store.drain_lines()
        if not lines:
            return self.path
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line)
                    handle.write("\n")
        except Exception:
            for line in lines:
                self.store.enqueue_line(line)
        return self.path

    def _flush_messages(self) -> None:
        messages = self.store.drain_messages()
        if not messages:
            return
        for message in messages:
            if not self._emit_to_sink(message):
                self.store.enqueue_message(message)
                break

    def _emit_to_sink(self, message: DebugMessage) -> bool:
        sink = self._sink_override
        if sink is not None:
            publish = getattr(sink, "publish", None)
            if callable(publish):
                try:
                    publish(message)
                except Exception:
                    return False
                return True
        buffer = self.runtime_debug_buffer
        publish = getattr(buffer, "publish", None)
        if not callable(publish):
            return False
        try:
            publish(message)
        except Exception:
            return False
        return True


def configure_leaf_debug_logging(
    *,
    runtime: dict[str, object] | None,
    group_name: str,
    worker_id: str,
    service: LeafLifecycleDebugLoggingService | None = None,
) -> Path | None:
    if service is None:
        return None
    return service.configure(runtime=runtime, group_name=group_name, worker_id=worker_id)


def bind_leaf_debug_sink(
    sink: object | None,
    *,
    service: LeafLifecycleDebugLoggingService | None = None,
) -> None:
    if service is None:
        return
    service.bind_sink(sink)


def leaf_debug_log(
    *,
    event: str,
    service: LeafLifecycleDebugLoggingService | None = None,
    **fields: object,
) -> None:
    if service is None:
        return
    safe_fields = {key: _json_safe(value) for key, value in fields.items()}
    service.log(event=event, **safe_fields)


def flush_leaf_debug_logging(
    *,
    service: LeafLifecycleDebugLoggingService | None = None,
) -> Path | None:
    if service is None:
        return None
    return service.flush()


def _resolve_debug_path(
    *,
    debug_cfg: dict[str, object],
    group_name: str,
    worker_id: str,
    write_to_file: bool,
) -> Path | None:
    if not write_to_file:
        return None
    logs_dir = debug_cfg.get("leaf_debug_logs_dir")
    if not isinstance(logs_dir, str) or not logs_dir:
        logs_dir = "logs/leaf_debug"
    path = Path(logs_dir) / _leaf_debug_file_name(group_name=group_name, worker_id=worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _serialize_line(
    *,
    event: str,
    fields: dict[str, object],
    group_name: str | None,
    worker_id: str | None,
) -> str | None:
    payload: dict[str, object] = {
        "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "event": event,
        "group_name": group_name,
        "worker_id": worker_id,
    }
    payload.update(fields)
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None


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


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if isinstance(value, str) and value:
        return value
    return None


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
    "LeafLifecycleDebugStore",
    "InMemoryLeafLifecycleDebugStore",
    "LeafLifecycleDebugLoggingService",
    "DefaultLeafLifecycleDebugLoggingService",
    "configure_leaf_debug_logging",
    "bind_leaf_debug_sink",
    "flush_leaf_debug_logging",
    "leaf_debug_log",
]
