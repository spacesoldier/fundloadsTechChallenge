from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from fund_load.kernel.trace import TraceRecord
from fund_load.ports.trace_sink import TraceSink


class JsonlTraceSink(TraceSink):
    # JsonlTraceSink writes one TraceRecord per line (Trace spec §7).
    def __init__(
        self,
        *,
        path: Path,
        write_mode: Literal["line", "batch"] = "line",
        flush_every_n: int = 1,
        flush_every_ms: int | None = None,
        fsync_every_n: int | None = None,
    ) -> None:
        self._path = path
        self._write_mode = write_mode
        self._flush_every_n = max(1, flush_every_n)
        self._flush_every_ms = flush_every_ms  # not implemented; reserved by spec
        self._fsync_every_n = fsync_every_n
        self._emit_count = 0
        self._buffer: list[str] = []
        self._handle = self._path.open("a", encoding="utf-8")

    def emit(self, record: "TraceRecord") -> None:
        # Serialize with stable keys and UTF-8 JSONL (Trace spec §7.2).
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        if self._write_mode == "batch":
            self._buffer.append(line)
            if len(self._buffer) >= self._flush_every_n:
                self._write_lines(self._buffer)
                self._buffer.clear()
        else:
            self._write_lines([line])
            if self._emit_count % self._flush_every_n == 0:
                self.flush()
        self._emit_count += 1
        if self._fsync_every_n and self._emit_count % self._fsync_every_n == 0:
            os.fsync(self._handle.fileno())

    def flush(self) -> None:
        # Flush both buffered and handle-level writes.
        if self._buffer:
            self._write_lines(self._buffer)
            self._buffer.clear()
        self._handle.flush()

    def close(self) -> None:
        # Close releases file descriptor; always flush pending data first.
        self.flush()
        self._handle.close()

    def _write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._handle.write(line + "\n")


class StdoutTraceSink(TraceSink):
    # StdoutTraceSink prints one JSON record per line (Trace spec §6.2).
    def emit(self, record: "TraceRecord") -> None:
        line = json.dumps(
            _trace_to_dict(record),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        sys.stdout.write(line + "\n")

    def flush(self) -> None:
        sys.stdout.flush()

    def close(self) -> None:
        self.flush()


def _trace_to_dict(record: "TraceRecord") -> dict[str, object]:
    # Build dict with stable key order and explicit field mapping (Trace spec §7.2).
    return {
        "trace_id": record.trace_id,
        "scenario": record.scenario,
        "line_no": record.line_no,
        "step_index": record.step_index,
        "step_name": record.step_name,
        "work_index": record.work_index,
        "t_enter": _format_dt(record.t_enter),
        "t_exit": _format_dt(record.t_exit),
        "duration_ms": record.duration_ms,
        "msg_in": _as_dict(record.msg_in),
        "msg_out": [_as_dict(item) for item in record.msg_out],
        "msg_out_count": record.msg_out_count,
        "ctx_before": record.ctx_before,
        "ctx_after": record.ctx_after,
        "ctx_diff": record.ctx_diff,
        "status": record.status,
        "error": _as_dict(record.error) if record.error is not None else None,
    }


def _as_dict(obj: object) -> object:
    # Support dataclasses for MessageSignature/ErrorInfo; passthrough for other types.
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _format_dt(value: datetime) -> str:
    # RFC3339 UTC format with Z suffix (Trace spec §7.2).
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(obj: object) -> str:
    # JSON fallback for deterministic serialization.
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)
