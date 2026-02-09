from __future__ import annotations

from stream_kernel.platform.discovery import platform_discovery_modules


def discovery_modules() -> list[str]:
    # Extension provider contract used by app.extensions.framework_discovery_modules().
    return platform_discovery_modules()


__all__ = ["discovery_modules", "platform_discovery_modules"]
