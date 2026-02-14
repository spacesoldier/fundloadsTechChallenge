from __future__ import annotations

import json
from pathlib import Path

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.logging import LogMessage


class StdoutLogSink:
    # Minimal structured log sink for platform-level stream logging channel.
    def emit(self, message: LogMessage) -> None:
        print(json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False))


class JsonlLogSink:
    # File-backed structured log sink for lifecycle/process diagnostics.
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a", encoding="utf-8")

    def emit(self, message: LogMessage) -> None:
        payload = json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False)
        self._file.write(payload + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


@adapter(name="log_stdout", consumes=[LogMessage], emits=[], binds=[("stream", LogMessage)])
def log_stdout(settings: dict[str, object]) -> StdoutLogSink:
    # Framework-owned stdout logging sink over standard stream channel.
    _ = settings
    return StdoutLogSink()


@adapter(name="log_jsonl", consumes=[LogMessage], emits=[], binds=[("stream", LogMessage)])
def log_jsonl(settings: dict[str, object]) -> JsonlLogSink:
    # Framework-owned JSONL logging sink for process lifecycle/diagnostics.
    path = settings.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("log_jsonl.settings.path must be a non-empty string")
    return JsonlLogSink(Path(path))


def _log_to_dict(message: LogMessage) -> dict[str, object]:
    return {
        "level": message.level,
        "message": message.message,
        "timestamp": message.timestamp.isoformat().replace("+00:00", "Z"),
        "fields": message.fields,
    }
