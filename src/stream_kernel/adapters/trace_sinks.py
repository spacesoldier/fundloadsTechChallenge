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
    from stream_kernel.kernel.trace import TraceRecord


class JsonlTraceSink:
    # JsonlTraceSink writes one TraceRecord per line (trace runtime docs).
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
        self._flush_every_ms = flush_every_ms  # reserved, not used in sync runtime
        self._fsync_every_n = fsync_every_n
        self._emit_count = 0
        self._buffer: list[str] = []
        self._handle = self._path.open("a", encoding="utf-8")

    def emit(self, record: "TraceRecord") -> None:
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
        if self._buffer:
            self._write_lines(self._buffer)
            self._buffer.clear()
        self._handle.flush()

    def close(self) -> None:
        self.flush()
        self._handle.close()

    def _write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._handle.write(line + "\n")


class StdoutTraceSink:
    # StdoutTraceSink prints one JSON record per line for local debugging.
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
    # Keep key order stable so diffs stay deterministic in tests and diagnostics.
    return {
        "trace_id": record.trace_id,
        "scenario": record.scenario,
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
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _format_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(obj: object) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)
