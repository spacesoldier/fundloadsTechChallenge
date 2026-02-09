from .domain import LogMessage, MonitoringMessage, TelemetryMessage, TraceMessage
from .observers import TracingObserver


def discovery_modules() -> list[str]:
    # Framework extension entrypoint: modules contributing adapters/observers for discovery.
    return [
        "stream_kernel.observability.adapters",
        "stream_kernel.observability.observers",
    ]


__all__ = [
    "TraceMessage",
    "LogMessage",
    "TelemetryMessage",
    "MonitoringMessage",
    "TracingObserver",
    "discovery_modules",
]
