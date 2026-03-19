"""
Redis publisher for simulation events.

Writes events in the format consumed by research_ui's debug vitrine ingest:
  {prefix}:runs:index:by_time         ZSET  run_id → epoch_ms
  {prefix}:runs:meta:{run_id}         HASH  {logical_run_id, total_records, first_ts, last_ts}
  {prefix}:runs:{run_id}:processes    SET   of process_ids
  {prefix}:runs:{run_id}:debug:{pid}  STREAM entries: {payload: <json>}

Process ID convention for simulation:
  root:sim:w0          — root sender process
  leaf:sim:w{leaf_id}  — leaf#N process

Each published event JSON matches the research_ui normalized event schema:
  timestamp, event, source, run_id, run_instance_id,
  process_role, process_group, worker_id, execution_group, worker_index,
  trace_id, fields
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_HOST = os.getenv("RESEARCH_UI_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("RESEARCH_UI_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("RESEARCH_UI_REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("RESEARCH_UI_REDIS_PASSWORD") or None
KEY_PREFIX = os.getenv("RESEARCH_UI_REDIS_KEY_PREFIX", "stream_kernel:debug")

# How many simulation events to buffer before flushing to Redis.
# Leaf processes accumulate per-tick samples and flush at the end of the scenario.
FLUSH_BATCH = 200


# ---------------------------------------------------------------------------
# SimPublisher
# ---------------------------------------------------------------------------

@dataclass
class SimPublisher:
    """
    Thread-safe (not really needed — each process has its own instance) publisher
    that writes simulation events to Redis.
    """
    run_id: str
    process_id: str          # e.g. "root:sim:w0" or "leaf:sim:w3"
    process_role: str        # "root" or "leaf"
    worker_index: int        # 0 for root, leaf_id for leaf

    _pending: list[dict[str, Any]] = field(default_factory=list, init=False)
    _redis: Any = field(default=None, init=False)
    _connected: bool = field(default=False, init=False)

    def connect(self) -> bool:
        try:
            import redis as redis_lib
            self._redis = redis_lib.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                socket_connect_timeout=1.0,
                socket_timeout=2.0,
            )
            self._redis.ping()
            self._connected = True
        except Exception:
            self._connected = False
        return self._connected

    def publish(
        self,
        event: str,
        fields: dict[str, object],
        *,
        ts: datetime | None = None,
    ) -> None:
        if ts is None:
            ts = datetime.now(tz=UTC)
        record = {
            "timestamp": ts.isoformat(),
            "event": event,
            "source": f"simulation.{self.process_role}",
            "run_id": "sim",
            "run_instance_id": self.run_id,
            "process_role": self.process_role,
            "process_group": "sim",
            "worker_id": f"{self.process_role}:sim:w{self.worker_index}",
            "execution_group": "sim",
            "worker_index": str(self.worker_index),
            "trace_id": None,
            "fields": fields,
        }
        self._pending.append(record)
        if len(self._pending) >= FLUSH_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self._connected or not self._pending:
            return
        try:
            r = self._redis
            stream_key = f"{KEY_PREFIX}:runs:{self.run_id}:debug:{self.process_id}"
            pipe = r.pipeline(transaction=False)
            for record in self._pending:
                pipe.xadd(stream_key, {"payload": json.dumps(record)})
            # Register process in the run's process set
            pipe.sadd(f"{KEY_PREFIX}:runs:{self.run_id}:processes", self.process_id)
            pipe.execute()
            self._pending.clear()
        except Exception:
            self._pending.clear()  # drop on error — sim continues

    def close(self, *, total_events: int = 0) -> None:
        self.flush()
        if not self._connected:
            return
        try:
            r = self._redis
            now_iso = datetime.now(tz=UTC).isoformat()
            r.hset(
                f"{KEY_PREFIX}:runs:meta:{self.run_id}",
                mapping={
                    "logical_run_id": self.run_id,
                    "total_records": total_events,
                    "last_ts": now_iso,
                },
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Run registration (called once by root before forking)
# ---------------------------------------------------------------------------

def register_run(run_id: str, label: str) -> bool:
    """
    Register the run in Redis index so research_ui picks it up.
    Returns True if successful.
    """
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD,
            socket_connect_timeout=1.0, socket_timeout=2.0,
        )
        now_ms = int(time.time() * 1000)
        now_iso = datetime.now(tz=UTC).isoformat()
        r.zadd(f"{KEY_PREFIX}:runs:index:by_time", {run_id: now_ms})
        r.hset(
            f"{KEY_PREFIX}:runs:meta:{run_id}",
            mapping={
                "logical_run_id": run_id,
                "label": label,
                "total_records": 0,
                "first_ts": now_iso,
                "last_ts": now_iso,
            },
        )
        return True
    except Exception:
        return False


def finalize_run(run_id: str, total_events: int) -> None:
    """Update run metadata after all processes have finished."""
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD,
            socket_connect_timeout=1.0, socket_timeout=2.0,
        )
        r.hset(
            f"{KEY_PREFIX}:runs:meta:{run_id}",
            mapping={
                "total_records": total_events,
                "last_ts": datetime.now(tz=UTC).isoformat(),
            },
        )
    except Exception:
        pass
