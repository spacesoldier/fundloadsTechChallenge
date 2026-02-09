from __future__ import annotations

from pathlib import Path

from stream_kernel.adapters.contracts import adapter
from stream_kernel.adapters.trace_sinks import JsonlTraceSink, StdoutTraceSink
from stream_kernel.observability.domain.tracing import TraceMessage


@adapter(name="trace_stdout", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
def trace_stdout(settings: dict[str, object]) -> StdoutTraceSink:
    # Framework-owned stdout trace sink adapter.
    _ = settings
    return StdoutTraceSink()


@adapter(name="trace_jsonl", consumes=[TraceMessage], emits=[], binds=[("kv_stream", TraceMessage)])
def trace_jsonl(settings: dict[str, object]) -> JsonlTraceSink:
    # Framework-owned JSONL trace sink adapter.
    path = settings.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("trace_jsonl.settings.path must be a non-empty string")
    return JsonlTraceSink(
        path=Path(path),
        write_mode=str(settings.get("write_mode", "line")),
        flush_every_n=int(settings.get("flush_every_n", 1)),
        flush_every_ms=settings.get("flush_every_ms") if isinstance(settings.get("flush_every_ms"), int) else None,
        fsync_every_n=settings.get("fsync_every_n") if isinstance(settings.get("fsync_every_n"), int) else None,
    )
