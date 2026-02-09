from __future__ import annotations

from stream_kernel.adapters.contracts import adapter
from stream_kernel.observability.domain.monitoring import MonitoringMessage


class StdoutMonitoringSink:
    # Minimal monitoring sink for runtime health/status events.
    def emit(self, message: MonitoringMessage) -> None:
        print(f"{message.name}:{message.status}")


@adapter(name="monitoring_stdout", consumes=[MonitoringMessage], emits=[], binds=[("stream", MonitoringMessage)])
def monitoring_stdout(settings: dict[str, object]) -> StdoutMonitoringSink:
    # Framework-owned stdout monitoring sink over standard stream channel.
    _ = settings
    return StdoutMonitoringSink()
