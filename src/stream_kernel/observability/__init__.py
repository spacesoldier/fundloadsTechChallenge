from .domain import LogMessage, MonitoringMessage, TelemetryMessage, TraceMessage


def discovery_modules() -> list[str]:
    # Framework extension entrypoint: modules contributing adapters for discovery.
    return [
        "stream_kernel.observability.adapters",
    ]


__all__ = [
    "TraceMessage",
    "LogMessage",
    "TelemetryMessage",
    "MonitoringMessage",
    "discovery_modules",
]
