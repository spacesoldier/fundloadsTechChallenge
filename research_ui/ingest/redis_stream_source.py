from __future__ import annotations

from research_ui.debug_vitrine.source import RedisDebugSource


class RedisStreamDebugSource(RedisDebugSource):
    """Redis Streams pull source for debug vitrine ingest."""


__all__ = ["RedisStreamDebugSource"]
