from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.logging import LogMessage


class StdoutLogSink:
    # Minimal structured log sink for platform-level stream logging channel.
    def emit(self, message: LogMessage) -> None:
        print(json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False))

    async def emit_async(self, message: LogMessage) -> None:
        await asyncio.to_thread(self.emit, message)


class StdoutPlainLogSink:
    # Human-readable lifecycle sink for local operator diagnostics.
    def emit(self, message: LogMessage) -> None:
        label = _process_label(message.fields)
        level = message.level.upper()
        line = f"[{label}]: [{level}]: {message.message}"
        extras = _plain_extras(message.fields)
        if extras:
            line = f"{line} | {extras}"
        print(line)

    async def emit_async(self, message: LogMessage) -> None:
        await asyncio.to_thread(self.emit, message)


class PlainFileLogSink:
    # File-backed plain-text logger matching stdout_plain format.
    def __init__(
        self,
        path: Path,
        *,
        flush_every_n: int = 1,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a", encoding="utf-8")
        self._emit_count = 0
        self._flush_every_n = max(1, int(flush_every_n))
        self._fsync_every_n = fsync_every_n if isinstance(fsync_every_n, int) and fsync_every_n > 0 else None

    def emit(self, message: LogMessage) -> None:
        label = _process_label(message.fields)
        level = message.level.upper()
        line = f"[{label}]: [{level}]: {message.message}"
        extras = _plain_extras(message.fields)
        if extras:
            line = f"{line} | {extras}"
        self._file.write(line + "\n")
        self._emit_count += 1
        if self._emit_count % self._flush_every_n == 0:
            self._file.flush()
        if self._fsync_every_n and self._emit_count % self._fsync_every_n == 0:
            os.fsync(self._file.fileno())

    async def emit_async(self, message: LogMessage) -> None:
        await asyncio.to_thread(self.emit, message)

    def close(self) -> None:
        self._file.flush()
        self._file.close()


def resolve_log_output_path(
    settings: dict[str, object],
    *,
    default_prefix: str,
    extension: str,
) -> Path:
    path = settings.get("path")
    if isinstance(path, str) and path:
        return Path(path)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = settings.get("file_prefix")
    if not isinstance(prefix, str) or not prefix:
        prefix = default_prefix
    safe_prefix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in prefix)
    return Path("logs") / f"{safe_prefix}_{ts}_pid{os.getpid()}.{extension}"


class JsonlLogSink:
    # File-backed structured log sink for lifecycle/process diagnostics.
    def __init__(
        self,
        path: Path,
        *,
        flush_every_n: int = 1,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a", encoding="utf-8")
        self._emit_count = 0
        self._flush_every_n = max(1, int(flush_every_n))
        self._fsync_every_n = fsync_every_n if isinstance(fsync_every_n, int) and fsync_every_n > 0 else None

    def emit(self, message: LogMessage) -> None:
        payload = json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False)
        self._file.write(payload + "\n")
        self._emit_count += 1
        if self._emit_count % self._flush_every_n == 0:
            self._file.flush()
        if self._fsync_every_n and self._emit_count % self._fsync_every_n == 0:
            os.fsync(self._file.fileno())

    async def emit_async(self, message: LogMessage) -> None:
        await asyncio.to_thread(self.emit, message)

    def close(self) -> None:
        self._file.flush()
        self._file.close()


@adapter(
    name="log_stdout",
    consumes=[LogMessage],
    emits=[],
    binds=[("stream", LogMessage)],
    execution_mode="async",
)
def log_stdout(settings: dict[str, object]) -> StdoutLogSink:
    # Framework-owned stdout logging sink over standard stream channel.
    _ = settings
    return StdoutLogSink()


@adapter(
    name="log_stdout_plain",
    consumes=[LogMessage],
    emits=[],
    binds=[("stream", LogMessage)],
    execution_mode="async",
)
def log_stdout_plain(settings: dict[str, object]) -> StdoutPlainLogSink:
    # Human-readable stdout logger with deterministic prefix format.
    _ = settings
    return StdoutPlainLogSink()


@adapter(
    name="log_jsonl",
    consumes=[LogMessage],
    emits=[],
    binds=[("stream", LogMessage)],
    execution_mode="async",
)
def log_jsonl(settings: dict[str, object]) -> JsonlLogSink:
    # Framework-owned JSONL logging sink for process lifecycle/diagnostics.
    path = resolve_log_output_path(settings, default_prefix="lifecycle", extension="jsonl")
    flush_every_n = settings.get("flush_every_n", 1)
    if not isinstance(flush_every_n, int) or flush_every_n <= 0:
        raise ValueError("log_jsonl.settings.flush_every_n must be an integer > 0 when provided")
    fsync_every_n = settings.get("fsync_every_n")
    if fsync_every_n is not None and (not isinstance(fsync_every_n, int) or fsync_every_n <= 0):
        raise ValueError("log_jsonl.settings.fsync_every_n must be an integer > 0 when provided")
    return JsonlLogSink(path, flush_every_n=flush_every_n, fsync_every_n=fsync_every_n)


@adapter(
    name="log_file_plain",
    consumes=[LogMessage],
    emits=[],
    binds=[("stream", LogMessage)],
    execution_mode="async",
)
def log_file_plain(settings: dict[str, object]) -> PlainFileLogSink:
    # Framework-owned plain-text file sink with stdout_plain-compatible format.
    path = resolve_log_output_path(settings, default_prefix="lifecycle", extension="log")
    flush_every_n = settings.get("flush_every_n", 1)
    if not isinstance(flush_every_n, int) or flush_every_n <= 0:
        raise ValueError("log_file_plain.settings.flush_every_n must be an integer > 0 when provided")
    fsync_every_n = settings.get("fsync_every_n")
    if fsync_every_n is not None and (not isinstance(fsync_every_n, int) or fsync_every_n <= 0):
        raise ValueError("log_file_plain.settings.fsync_every_n must be an integer > 0 when provided")
    return PlainFileLogSink(path, flush_every_n=flush_every_n, fsync_every_n=fsync_every_n)


def _log_to_dict(message: LogMessage) -> dict[str, object]:
    return {
        "level": message.level,
        "message": message.message,
        "timestamp": message.timestamp.isoformat().replace("+00:00", "Z"),
        "fields": message.fields,
    }


def _process_label(fields: dict[str, object]) -> str:
    process_name = fields.get("process_name")
    worker_id = fields.get("worker_id")
    worker_pid = fields.get("worker_pid")
    pid = fields.get("pid")
    base = None
    if process_name == "supervisor":
        base = "supervisor"
    elif isinstance(worker_id, str) and worker_id:
        base = worker_id
    elif isinstance(process_name, str) and process_name:
        base = process_name
    else:
        base = "process"
    if isinstance(worker_pid, int):
        return f"{base}-{worker_pid}"
    if isinstance(pid, int):
        return f"{base}-{pid}"
    return base


def _plain_extras(fields: dict[str, object]) -> str:
    omit = {"process_name", "worker_id", "pid", "worker_pid"}
    parts: list[str] = []
    for key in sorted(fields):
        if key in omit:
            continue
        parts.append(f"{key}={fields[key]}")
    return " ".join(parts)
