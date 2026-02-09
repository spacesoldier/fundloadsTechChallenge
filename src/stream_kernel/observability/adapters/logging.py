from __future__ import annotations

import json

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.logging import LogMessage


class StdoutLogSink:
    # Minimal structured log sink for platform-level stream logging channel.
    def emit(self, message: LogMessage) -> None:
        print(json.dumps(_log_to_dict(message), separators=(",", ":"), ensure_ascii=False))


@adapter(name="log_stdout", consumes=[LogMessage], emits=[], binds=[("stream", LogMessage)])
def log_stdout(settings: dict[str, object]) -> StdoutLogSink:
    # Framework-owned stdout logging sink over standard stream channel.
    _ = settings
    return StdoutLogSink()


def _log_to_dict(message: LogMessage) -> dict[str, object]:
    return {
        "level": message.level,
        "message": message.message,
        "timestamp": message.timestamp.isoformat().replace("+00:00", "Z"),
        "fields": message.fields,
    }
