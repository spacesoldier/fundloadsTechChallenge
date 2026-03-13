from __future__ import annotations

import atexit
import json
import os
import socket
from datetime import UTC, datetime
from queue import Empty, SimpleQueue
from threading import Event, Thread
from typing import Any

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.adapters.logging import (
    _encode_redis_command,
    _read_redis_reply,
    _resolve_run_identity,
    _to_redis_string,
)
from stream_kernel.observability.domain.debug import DebugMessage


class RedisDebugSink:
    # Redis-backed sink for DebugMessage stream (writes to Redis Streams).
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
        write_mode: str,
        queue_max_items: int,
        batch_max_items: int,
        batch_flush_interval_ms: int,
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
        self._session_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ") + f"-pid{os.getpid()}"
        self._write_mode = write_mode
        self._queue_max_items = max(1, int(queue_max_items))
        self._batch_max_items = max(1, int(batch_max_items))
        self._batch_flush_interval_ms = max(1, int(batch_flush_interval_ms))
        self._bg_queue: SimpleQueue[list[list[object]]] = SimpleQueue()
        self._bg_wake = Event()
        self._bg_closed = Event()
        self._bg_thread: Thread | None = None
        if self._write_mode == "background":
            self._bg_thread = Thread(
                name=f"sk-debug-redis-writer-{os.getpid()}",
                target=self._run_background_writer,
                daemon=True,
            )
            self._bg_thread.start()
            atexit.register(self.close)

    def emit(self, message: DebugMessage) -> None:
        if not isinstance(message, DebugMessage):
            return
        fields = message.fields if isinstance(message.fields, dict) else {}
        run_id, logical_run_id = _resolve_run_identity(
            fields={
                "__run_instance_id": message.run_instance_id,
                "__run_id": message.run_id,
                **fields,
            },
            session_id=self._session_id,
        )
        process_fields = {
            "process_group": message.process_group,
            "worker_id": message.worker_id,
            "process_name": message.worker_id or message.process_group or "unknown",
        }
        process_fields.update(fields)
        process_id, process_role, execution_group, worker_index = _resolve_process_identity(process_fields)
        timestamp = message.timestamp if isinstance(message.timestamp, datetime) else datetime.now(tz=UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        ts_iso = timestamp.isoformat().replace("+00:00", "Z")
        ts_epoch_ms = int(timestamp.timestamp() * 1000)

        record_key = f"{self._key_prefix}:runs:{run_id}:debug:{process_id}"
        run_processes_key = f"{self._key_prefix}:runs:{run_id}:processes"
        run_processes_by_time_key = f"{self._key_prefix}:runs:{run_id}:processes:by_time"
        run_meta_key = f"{self._key_prefix}:runs:meta:{run_id}"
        run_reports_key = f"{self._key_prefix}:runs:reports"
        runs_by_time_key = f"{self._key_prefix}:runs:index:by_time"
        process_logs_index_key = f"{self._key_prefix}:debug:index:process"
        process_logs_by_time_key = f"{self._key_prefix}:debug:index:by_time"
        process_member = f"{run_id}:{process_id}"
        payload = json.dumps(
            _debug_to_dict(
                message,
                process_role=process_role,
                execution_group=execution_group,
                worker_index=worker_index,
            ),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        commands: list[list[object]] = [
            ["XADD", record_key, "*", "payload", payload],
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
                if key in {"debug_summary"}:
                    continue
                commands.append(["HSET", run_meta_key, f"summary:{key}", _to_redis_string(value)])
            if self._ttl_seconds > 0:
                commands.append(["EXPIRE", run_meta_key, self._ttl_seconds])
        if self._write_mode == "background":
            self._enqueue_background(commands)
            return
        self._execute(commands)

    async def emit_async(self, message: DebugMessage) -> None:
        self.emit(message)

    def close(self) -> None:
        if self._write_mode != "background":
            return
        if self._bg_closed.is_set():
            return
        self._bg_closed.set()
        self._bg_wake.set()
        if isinstance(self._bg_thread, Thread) and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=max(0.1, self._socket_timeout_seconds * 2.0))

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

    def _enqueue_background(self, commands: list[list[object]]) -> None:
        if not commands:
            return
        if self._bg_closed.is_set():
            return
        self._bg_queue.put(commands)
        self._bg_wake.set()

    def _run_background_writer(self) -> None:
        wait_seconds = max(0.001, float(self._batch_flush_interval_ms) / 1000.0)
        while True:
            self._bg_wake.wait(wait_seconds)
            self._bg_wake.clear()
            batch: list[list[object]] = []
            while len(batch) < self._batch_max_items:
                try:
                    batch.extend(self._bg_queue.get_nowait())
                except Empty:
                    break
            if batch:
                try:
                    self._execute(batch)
                except Exception:
                    # Debug sink must be best-effort and never break runtime loop.
                    pass
            if self._bg_closed.is_set() and self._bg_queue.empty():
                break


@adapter(
    name="debug_redis",
    consumes=[DebugMessage],
    emits=[],
    binds=[("stream", DebugMessage)],
    execution_mode="async",
)
def debug_redis(settings: dict[str, object]) -> RedisDebugSink:
    host = settings.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        raise ValueError("debug_redis.settings.host must be a non-empty string when provided")
    port = settings.get("port", 6379)
    if not isinstance(port, int) or port <= 0:
        raise ValueError("debug_redis.settings.port must be an integer > 0 when provided")
    db = settings.get("db", 0)
    if not isinstance(db, int) or db < 0:
        raise ValueError("debug_redis.settings.db must be an integer >= 0 when provided")
    password = settings.get("password")
    if password is not None and (not isinstance(password, str) or not password):
        raise ValueError("debug_redis.settings.password must be a non-empty string when provided")
    key_prefix = settings.get("key_prefix", "stream_kernel:debug")
    if not isinstance(key_prefix, str) or not key_prefix:
        raise ValueError("debug_redis.settings.key_prefix must be a non-empty string when provided")
    ttl_seconds = settings.get("ttl_seconds", 86400)
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("debug_redis.settings.ttl_seconds must be an integer > 0 when provided")
    connect_timeout_seconds = settings.get("connect_timeout_seconds", 0.2)
    if not isinstance(connect_timeout_seconds, (int, float)) or connect_timeout_seconds <= 0:
        raise ValueError("debug_redis.settings.connect_timeout_seconds must be a number > 0 when provided")
    socket_timeout_seconds = settings.get("socket_timeout_seconds", 1.0)
    if not isinstance(socket_timeout_seconds, (int, float)) or socket_timeout_seconds <= 0:
        raise ValueError("debug_redis.settings.socket_timeout_seconds must be a number > 0 when provided")
    write_mode = settings.get("write_mode", "background")
    if not isinstance(write_mode, str) or write_mode not in {"background", "inline"}:
        raise ValueError("debug_redis.settings.write_mode must be one of: ['background', 'inline']")
    queue_max_items = settings.get("queue_max_items", 8192)
    if not isinstance(queue_max_items, int) or queue_max_items <= 0:
        raise ValueError("debug_redis.settings.queue_max_items must be an integer > 0 when provided")
    batch_max_items = settings.get("batch_max_items", 64)
    if not isinstance(batch_max_items, int) or batch_max_items <= 0:
        raise ValueError("debug_redis.settings.batch_max_items must be an integer > 0 when provided")
    batch_flush_interval_ms = settings.get("batch_flush_interval_ms", 20)
    if not isinstance(batch_flush_interval_ms, int) or batch_flush_interval_ms <= 0:
        raise ValueError(
            "debug_redis.settings.batch_flush_interval_ms must be an integer > 0 when provided"
        )
    return RedisDebugSink(
        host=host,
        port=port,
        db=db,
        password=password if isinstance(password, str) and password else None,
        key_prefix=key_prefix,
        ttl_seconds=ttl_seconds,
        connect_timeout_seconds=float(connect_timeout_seconds),
        socket_timeout_seconds=float(socket_timeout_seconds),
        write_mode=write_mode,
        queue_max_items=queue_max_items,
        batch_max_items=batch_max_items,
        batch_flush_interval_ms=batch_flush_interval_ms,
    )


def _debug_to_dict(
    message: DebugMessage,
    *,
    process_role: str,
    execution_group: str,
    worker_index: str,
) -> dict[str, object]:
    return {
        "timestamp": message.timestamp.isoformat().replace("+00:00", "Z"),
        "event": message.event,
        "source": message.source,
        "run_id": message.run_id,
        "run_instance_id": message.run_instance_id,
        "process_role": process_role,
        "process_group": message.process_group,
        "worker_id": message.worker_id,
        "execution_group": execution_group,
        "worker_index": worker_index,
        "trace_id": message.trace_id,
        "fields": dict(message.fields),
    }


def _resolve_process_identity(fields: dict[str, object]) -> tuple[str, str, str, str]:
    worker_id_raw = fields.get("worker_id")
    process_group_raw = fields.get("process_group")
    process_name_raw = fields.get("process_name")
    worker_id = worker_id_raw if isinstance(worker_id_raw, str) and worker_id_raw else None
    process_group = process_group_raw if isinstance(process_group_raw, str) and process_group_raw else None
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
    process_id = f"{safe_role}:{safe_group}:w{safe_index}"
    return process_id, safe_role, safe_group, safe_index


def _safe_key_segment(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return normalized or fallback


__all__ = ["RedisDebugSink", "debug_redis"]
