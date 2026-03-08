from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.logging import LogMessage


class StdoutLogSink:
    # Minimal structured log sink for platform-level stream logging channel.
    def emit(self, message: LogMessage) -> None:
        print(json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False))

    async def emit_async(self, message: LogMessage) -> None:
        self.emit(message)


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
        self.emit(message)


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
        self.emit(message)

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
        self.emit(message)

    def close(self) -> None:
        self._file.flush()
        self._file.close()


class RedisDebugLogSink:
    # Redis-backed structured log sink for runtime debug events.
    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: int,
        password: str | None,
        key_prefix: str,
        ttl_seconds: int,
        connect_timeout_seconds: float,
        socket_timeout_seconds: float,
        only_debug_channel: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
        self._only_debug_channel = only_debug_channel
        self._session_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ") + f"-pid{os.getpid()}"

    def emit(self, message: LogMessage) -> None:
        fields = message.fields if isinstance(message.fields, dict) else {}
        if self._only_debug_channel and fields.get("debug_channel") != "runtime_debug":
            return
        run_id, logical_run_id = _resolve_run_identity(fields=fields, session_id=self._session_id)
        process_id = _safe_process_id(fields)
        timestamp = message.timestamp if isinstance(message.timestamp, datetime) else datetime.now(tz=UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        ts_iso = timestamp.isoformat().replace("+00:00", "Z")
        ts_epoch_ms = int(timestamp.timestamp() * 1000)

        record_key = f"{self._key_prefix}:runs:{run_id}:logs:{process_id}"
        run_processes_key = f"{self._key_prefix}:runs:{run_id}:processes"
        run_processes_by_time_key = f"{self._key_prefix}:runs:{run_id}:processes:by_time"
        run_meta_key = f"{self._key_prefix}:runs:meta:{run_id}"
        run_reports_key = f"{self._key_prefix}:runs:reports"
        runs_by_time_key = f"{self._key_prefix}:runs:index:by_time"
        process_logs_index_key = f"{self._key_prefix}:logs:index:process"
        process_logs_by_time_key = f"{self._key_prefix}:logs:index:by_time"
        process_member = f"{run_id}:{process_id}"
        payload = json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False)
        commands: list[list[object]] = [
            ["RPUSH", record_key, payload],
            ["SADD", run_processes_key, process_id],
            ["ZADD", run_processes_by_time_key, ts_epoch_ms, process_id],
            ["HSETNX", run_meta_key, "logical_run_id", logical_run_id],
            ["HSETNX", run_meta_key, "session_id", self._session_id],
            ["HSETNX", run_meta_key, "first_ts", ts_iso],
            ["HSET", run_meta_key, "last_ts", ts_iso],
            ["HSET", run_meta_key, "last_process", process_id],
            ["HINCRBY", run_meta_key, "total_records", 1],
            ["HSET", run_reports_key, run_id, run_meta_key],
            ["ZADD", runs_by_time_key, ts_epoch_ms, run_id],
            ["HSET", process_logs_index_key, process_member, record_key],
            ["ZADD", process_logs_by_time_key, ts_epoch_ms, process_member],
        ]
        if self._ttl_seconds > 0:
            commands.extend(
                [
                    ["EXPIRE", record_key, self._ttl_seconds],
                    ["EXPIRE", run_processes_key, self._ttl_seconds],
                    ["EXPIRE", run_processes_by_time_key, self._ttl_seconds],
                    ["EXPIRE", run_meta_key, self._ttl_seconds],
                ]
            )
        if fields.get("debug_summary") is True:
            for key, value in fields.items():
                if not isinstance(key, str) or not key:
                    continue
                if key in {"debug_channel", "debug_summary"}:
                    continue
                commands.append(["HSET", run_meta_key, f"summary:{key}", _to_redis_string(value)])
            if self._ttl_seconds > 0:
                commands.append(["EXPIRE", run_meta_key, self._ttl_seconds])
        self._execute(commands)

    async def emit_async(self, message: LogMessage) -> None:
        self.emit(message)

    def _execute(self, commands: list[list[object]]) -> None:
        if not commands:
            return
        with socket.create_connection(
            (self._host, self._port),
            timeout=self._connect_timeout_seconds,
        ) as conn:
            conn.settimeout(self._socket_timeout_seconds)
            prelude: list[list[object]] = []
            if isinstance(self._password, str) and self._password:
                prelude.append(["AUTH", self._password])
            prelude.append(["SELECT", self._db])
            wire = b"".join(_encode_redis_command(command) for command in prelude + commands)
            conn.sendall(wire)
            for _ in range(len(prelude) + len(commands)):
                if not _read_redis_reply(conn):
                    raise RuntimeError("redis reply indicates failure")


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


@adapter(
    name="log_redis_debug",
    consumes=[LogMessage],
    emits=[],
    binds=[("stream", LogMessage)],
    execution_mode="async",
)
def log_redis_debug(settings: dict[str, object]) -> RedisDebugLogSink:
    # Runtime debug sink over Redis lists/hashes.
    host = settings.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        raise ValueError("log_redis_debug.settings.host must be a non-empty string when provided")
    port = settings.get("port", 6379)
    if not isinstance(port, int) or port <= 0:
        raise ValueError("log_redis_debug.settings.port must be an integer > 0 when provided")
    db = settings.get("db", 0)
    if not isinstance(db, int) or db < 0:
        raise ValueError("log_redis_debug.settings.db must be an integer >= 0 when provided")
    password = settings.get("password")
    if password is not None and (not isinstance(password, str) or not password):
        raise ValueError("log_redis_debug.settings.password must be a non-empty string when provided")
    key_prefix = settings.get("key_prefix", "stream_kernel:debug")
    if not isinstance(key_prefix, str) or not key_prefix:
        raise ValueError("log_redis_debug.settings.key_prefix must be a non-empty string when provided")
    ttl_seconds = settings.get("ttl_seconds", 86400)
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("log_redis_debug.settings.ttl_seconds must be an integer > 0 when provided")
    connect_timeout_seconds = settings.get("connect_timeout_seconds", 0.2)
    if not isinstance(connect_timeout_seconds, (int, float)) or connect_timeout_seconds <= 0:
        raise ValueError(
            "log_redis_debug.settings.connect_timeout_seconds must be a number > 0 when provided"
        )
    socket_timeout_seconds = settings.get("socket_timeout_seconds", 1.0)
    if not isinstance(socket_timeout_seconds, (int, float)) or socket_timeout_seconds <= 0:
        raise ValueError("log_redis_debug.settings.socket_timeout_seconds must be a number > 0 when provided")
    capture_all_events = settings.get("capture_all_events")
    if capture_all_events is not None and not isinstance(capture_all_events, bool):
        raise ValueError("log_redis_debug.settings.capture_all_events must be a boolean when provided")
    only_debug_channel = settings.get("only_debug_channel", True)
    if not isinstance(only_debug_channel, bool):
        raise ValueError("log_redis_debug.settings.only_debug_channel must be a boolean when provided")
    if isinstance(capture_all_events, bool):
        only_debug_channel = not capture_all_events
    return RedisDebugLogSink(
        host=host,
        port=port,
        db=db,
        password=password if isinstance(password, str) and password else None,
        key_prefix=key_prefix,
        ttl_seconds=ttl_seconds,
        connect_timeout_seconds=float(connect_timeout_seconds),
        socket_timeout_seconds=float(socket_timeout_seconds),
        only_debug_channel=only_debug_channel,
    )


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


def _safe_run_id(raw: object) -> str:
    if isinstance(raw, str) and raw:
        return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return "run"


def _resolve_run_identity(*, fields: dict[str, object], session_id: str) -> tuple[str, str]:
    raw_run_id = fields.get("run_id")
    if not isinstance(raw_run_id, str) or not raw_run_id:
        raw_run_id = fields.get("__run_id")
    if not isinstance(raw_run_id, str) or not raw_run_id:
        raw_run_id = os.getenv("STREAM_KERNEL_LOGICAL_RUN_ID")
    logical_run_id = _safe_run_id(raw_run_id)
    # Default logical run ids like "run" are not unique across launches.
    # Prefer process-inherited launch id (shared by root/leafs), then per-sink session id.
    if logical_run_id == "run":
        launch_id = fields.get("__run_instance_id")
        if not isinstance(launch_id, str) or not launch_id:
            launch_id = os.getenv("STREAM_KERNEL_RUN_INSTANCE_ID")
        safe_launch_id = _safe_run_id(launch_id)
        if safe_launch_id and safe_launch_id != "run":
            return (f"{logical_run_id}:{safe_launch_id}", logical_run_id)
        return (f"{logical_run_id}:{session_id}", logical_run_id)
    return (logical_run_id, logical_run_id)


def _safe_process_id(fields: dict[str, object]) -> str:
    worker_id_raw = fields.get("worker_id")
    process_group_raw = fields.get("process_group")
    process_name_raw = fields.get("process_name")
    worker_id = worker_id_raw if isinstance(worker_id_raw, str) and worker_id_raw else os.getenv("STREAM_KERNEL_WORKER_ID")
    process_group = (
        process_group_raw
        if isinstance(process_group_raw, str) and process_group_raw
        else os.getenv("STREAM_KERNEL_PROCESS_GROUP")
    )
    process_name = process_name_raw if isinstance(process_name_raw, str) and process_name_raw else None

    if process_group is None and isinstance(worker_id, str) and "#" in worker_id:
        process_group = worker_id.split("#", 1)[0]
    if process_group is None and isinstance(process_name, str) and process_name:
        process_group = process_name
    if process_group is None:
        process_group = "supervisor" if isinstance(worker_id, str) and worker_id.startswith("supervisor#") else "unknown"

    worker_index = "1"
    if isinstance(worker_id, str) and "#" in worker_id:
        candidate = worker_id.rsplit("#", 1)[1]
        worker_index = candidate if candidate else "1"
    elif process_group != "supervisor":
        worker_index = "0"

    process_role = "root" if process_group == "supervisor" else "leaf"
    safe_role = _safe_key_segment(process_role, fallback="leaf")
    safe_group = _safe_key_segment(process_group, fallback="unknown")
    safe_index = _safe_key_segment(worker_index, fallback="0")
    return f"{safe_role}:{safe_group}:w{safe_index}"


def _safe_key_segment(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return normalized or fallback


def _encode_redis_command(parts: list[object]) -> bytes:
    chunks = [f"*{len(parts)}\r\n".encode("utf-8")]
    for part in parts:
        raw = _to_redis_string(part).encode("utf-8")
        chunks.append(f"${len(raw)}\r\n".encode("utf-8"))
        chunks.append(raw + b"\r\n")
    return b"".join(chunks)


def _read_redis_reply(conn: socket.socket) -> bool:
    prefix = _recv_exact(conn, 1)
    if not prefix:
        return False
    token = prefix.decode("ascii", errors="ignore")
    if token in {"+", "-", ":"}:
        _readline(conn)
        return token != "-"
    if token == "$":
        try:
            length = int(_readline(conn))
        except Exception:
            return False
        if length < 0:
            return True
        _recv_exact(conn, length + 2)
        return True
    if token == "*":
        try:
            count = int(_readline(conn))
        except Exception:
            return False
        if count <= 0:
            return True
        for _ in range(count):
            if not _read_redis_reply(conn):
                return False
        return True
    return False


def _readline(conn: socket.socket) -> str:
    data = bytearray()
    while True:
        chunk = _recv_exact(conn, 1)
        if not chunk:
            break
        data.extend(chunk)
        if data.endswith(b"\r\n"):
            break
    if data.endswith(b"\r\n"):
        data = data[:-2]
    return data.decode("utf-8", errors="ignore")


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    if size <= 0:
        return b""
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _to_redis_string(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return repr(value)
