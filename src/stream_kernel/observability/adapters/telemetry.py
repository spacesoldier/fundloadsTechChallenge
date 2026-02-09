from __future__ import annotations

import json

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.telemetry import TelemetryMessage


class StdoutTelemetrySink:
    # Minimal structured telemetry sink for platform-level stream telemetry channel.
    def emit(self, message: TelemetryMessage) -> None:
        print(json.dumps(_telemetry_to_dict(message), separators=(",", ":"), ensure_ascii=False))


@adapter(name="telemetry_stdout", consumes=[TelemetryMessage], emits=[], binds=[("stream", TelemetryMessage)])
def telemetry_stdout(settings: dict[str, object]) -> StdoutTelemetrySink:
    # Framework-owned stdout telemetry sink over standard stream channel.
    _ = settings
    return StdoutTelemetrySink()


def _telemetry_to_dict(message: TelemetryMessage) -> dict[str, object]:
    return {
        "metric": message.metric,
        "value": message.value,
        "timestamp": message.timestamp.isoformat().replace("+00:00", "Z"),
        "tags": message.tags,
    }
