from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Event, Thread

from research_ui.debug_vitrine.service import DebugVitrineService

_LOG = logging.getLogger("research_ui.debug_ingest")


@dataclass(slots=True)
class RedisDebugIngestWorker:
    service: DebugVitrineService
    enabled: bool
    interval_seconds: float
    limit_runs: int
    max_events_per_process: int
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if not self.enabled:
            _LOG.info("redis auto-ingest disabled")
            return
        if isinstance(self._thread, Thread) and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            name="research-ui-redis-ingest",
            target=self._run_loop,
            daemon=True,
        )
        self._thread.start()
        _LOG.info(
            "redis auto-ingest started interval_seconds=%s limit_runs=%s max_events_per_process=%s",
            self.interval_seconds,
            self.limit_runs,
            self.max_events_per_process,
        )

    def stop(self) -> None:
        self._stop.set()
        if isinstance(self._thread, Thread) and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, self.interval_seconds * 2.0))
        _LOG.info("redis auto-ingest stopped")

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.service.reload_from_redis(
                    limit_runs=self.limit_runs,
                    max_events_per_process=self.max_events_per_process,
                )
                loaded_runs = int(result.get("loaded_runs", 0))
                loaded_events = int(result.get("loaded_events", 0))
                if loaded_runs > 0 and loaded_events > 0:
                    _LOG.info(
                        "redis auto-ingest cycle loaded_runs=%s loaded_events=%s loaded_processes=%s",
                        loaded_runs,
                        loaded_events,
                        int(result.get("loaded_processes", 0)),
                    )
            except Exception:
                _LOG.exception("redis auto-ingest cycle failed")
            self._stop.wait(self.interval_seconds)


__all__ = ["RedisDebugIngestWorker"]
