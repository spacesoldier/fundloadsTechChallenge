from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fund_load.kernel.context import Context


@dataclass(frozen=True, slots=True)
class MessageSignature:
    # MessageSignature captures type + optional identity/hash (Trace spec §3.2).
    type_name: str
    identity: str | None
    hash: str | None


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    # ErrorInfo records step exceptions (Trace spec §3.3).
    type: str
    message: str
    where: str
    stack: str | None = None


@dataclass(frozen=True, slots=True)
class TraceRecord:
    # TraceRecord captures one step invocation for one message (Trace spec §3.1).
    trace_id: str
    scenario: str
    line_no: int | None
    step_index: int
    step_name: str
    work_index: int
    t_enter: datetime
    t_exit: datetime
    duration_ms: float
    msg_in: MessageSignature
    msg_out: tuple[MessageSignature, ...]
    msg_out_count: int
    ctx_before: dict[str, object] | None
    ctx_after: dict[str, object] | None
    ctx_diff: dict[str, object] | None
    status: Literal["ok", "error"]
    error: ErrorInfo | None


@dataclass(frozen=True, slots=True)
class TraceSpan:
    # TraceSpan is an internal handle used between step enter/exit.
    step_name: str
    step_index: int
    work_index: int
    msg_in: MessageSignature
    t_enter: datetime
    ctx_before: dict[str, object] | None


class TraceRecorder:
    # TraceRecorder emits TraceRecord entries and appends them to ctx.trace (Trace spec §2/3).
    def __init__(
        self,
        *,
        signature_mode: Literal["type_only", "type_and_identity", "hash"] = "type_only",
        context_diff_mode: Literal["none", "whitelist", "debug"] = "none",
        context_diff_whitelist: Iterable[str] = (),
        max_value_len: int = 256,
    ) -> None:
        self._signature_mode = signature_mode
        self._context_diff_mode = context_diff_mode
        self._context_diff_whitelist = tuple(context_diff_whitelist)
        self._max_value_len = max_value_len

    def begin(
        self,
        *,
        ctx: Context,
        step_name: str,
        step_index: int,
        work_index: int,
        msg_in: object,
    ) -> TraceSpan:
        # Snapshot context before the step if tracing config requests it.
        ctx_before = self._snapshot_context(ctx) if self._context_diff_mode != "none" else None
        return TraceSpan(
            step_name=step_name,
            step_index=step_index,
            work_index=work_index,
            msg_in=self._signature(msg_in),
            t_enter=datetime.now(tz=UTC),
            ctx_before=ctx_before,
        )

    def finish(
        self,
        *,
        ctx: Context,
        span: TraceSpan,
        msg_out: Iterable[object],
        status: Literal["ok", "error"],
        error: ErrorInfo | None,
    ) -> TraceRecord:
        # Build a record on step exit and append to ctx.trace for in-memory tape.
        t_exit = datetime.now(tz=UTC)
        out_list = list(msg_out)
        out_signatures = tuple(self._signature(item) for item in out_list)
        ctx_after = self._snapshot_context(ctx) if self._context_diff_mode != "none" else None
        ctx_diff = self._diff_context(span.ctx_before, ctx_after) if self._context_diff_mode != "none" else None
        record = TraceRecord(
            trace_id=ctx.trace_id,
            scenario=ctx.scenario_id,
            line_no=ctx.line_no,
            step_index=span.step_index,
            step_name=span.step_name,
            work_index=span.work_index,
            t_enter=span.t_enter,
            t_exit=t_exit,
            duration_ms=(t_exit - span.t_enter).total_seconds() * 1000.0,
            msg_in=span.msg_in,
            msg_out=out_signatures,
            msg_out_count=len(out_signatures),
            ctx_before=span.ctx_before,
            ctx_after=ctx_after,
            ctx_diff=ctx_diff,
            status=status,
            error=error,
        )
        ctx.trace.append(record)
        return record

    def _signature(self, msg: object) -> MessageSignature:
        # Signature is derived from config (Trace spec §4.1).
        type_name = type(msg).__name__
        identity = None
        digest = None
        if self._signature_mode in {"type_and_identity", "hash"}:
            identity = _extract_identity(msg)
        if self._signature_mode == "hash":
            digest = _hash_message(msg)
        return MessageSignature(type_name=type_name, identity=identity, hash=digest)

    def _snapshot_context(self, ctx: Context) -> dict[str, object]:
        # We keep snapshotting flat, top-level keys only (Trace spec §4.2).
        if self._context_diff_mode == "debug":
            snapshot = dataclasses.asdict(ctx)
        else:
            snapshot = {}
            for key in self._context_diff_whitelist:
                if hasattr(ctx, key):
                    snapshot[key] = copy.deepcopy(getattr(ctx, key))
        return _truncate_snapshot(snapshot, self._max_value_len)

    def _diff_context(
        self, before: dict[str, object] | None, after: dict[str, object] | None
    ) -> dict[str, object]:
        # We use a simple replace-entire-value strategy for top-level keys (Trace spec §11.5).
        if before is None or after is None:
            return {}
        diff: dict[str, object] = {}
        for key in sorted(set(before.keys()) | set(after.keys())):
            if before.get(key) != after.get(key):
                diff[key] = {"before": before.get(key), "after": after.get(key)}
        return diff


def _extract_identity(msg: object) -> str | None:
    # Identity prefers "id" attributes/keys; fallback to None (Trace spec §4.1).
    if isinstance(msg, dict) and "id" in msg:
        return str(msg["id"])
    if hasattr(msg, "id"):
        return str(getattr(msg, "id"))
    return None


def _message_snapshot(msg: object) -> object:
    # Canonical snapshot supports dataclasses, dicts, and objects with __dict__.
    if dataclasses.is_dataclass(msg):
        return dataclasses.asdict(msg)
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "__dict__"):
        return dict(vars(msg))
    return {"value": str(msg)}


def _hash_message(msg: object) -> str:
    # Hashing uses a deterministic JSON representation (Trace spec §4.1).
    snapshot = _message_snapshot(msg)
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_default(obj: object) -> str:
    # JSON fallback for deterministic hashing (Time/Money semantics doc).
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


def _truncate_snapshot(snapshot: dict[str, object], max_len: int) -> dict[str, object]:
    # Truncate long string values to keep trace payload bounded (Trace spec §11.9).
    truncated: dict[str, object] = {}
    for key, value in snapshot.items():
        if isinstance(value, str) and len(value) > max_len:
            truncated[key] = value[:max_len] + "...(truncated)"
        else:
            truncated[key] = value
    return truncated
