from __future__ import annotations


def platform_discovery_modules() -> list[str]:
    # Central framework discovery entrypoint for platform-managed modules.
    return [
        "stream_kernel.adapters.file_io",
        "stream_kernel.platform.services",
        "stream_kernel.integration.work_queue",
        "stream_kernel.routing.routing_service",
        "stream_kernel.observability.adapters",
        "stream_kernel.observability.observers",
    ]
