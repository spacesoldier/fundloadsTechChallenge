"""External API stage: concurrency semaphore + lognormal latency + circuit breaker."""
from __future__ import annotations
import asyncio
import math
import time
from collections import deque

import numpy as np

_rng = np.random.default_rng()


def _lognormal_ms(mean_ms: float, sigma: float) -> float:
    mu = math.log(mean_ms)
    return float(np.exp(_rng.normal(mu, sigma)))


class CircuitBreaker:
    """
    States: closed → open → half_open → closed.
    failure_threshold : fraction of recent calls that must fail to trip.
    window_size       : number of recent calls tracked.
    cooldown_s        : seconds in OPEN before trying HALF_OPEN.
    """

    def __init__(
        self,
        failure_threshold: float = 0.5,
        window_size: int = 20,
        cooldown_s: float = 5.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_size       = window_size
        self.cooldown_s        = cooldown_s
        self._state            = "closed"
        self._history: deque[bool] = deque(maxlen=window_size)
        self._open_at: float       = 0.0
        self.trips: int            = 0
        self.fast_fails: int       = 0

    @property
    def state(self) -> str:
        return self._state

    def allow_request(self) -> bool:
        now = asyncio.get_event_loop().time()
        if self._state == "closed":
            return True
        if self._state == "open":
            if now - self._open_at >= self.cooldown_s:
                self._state = "half_open"
                return True
            self.fast_fails += 1
            return False
        # half_open: allow one probe
        return True

    def record_success(self) -> None:
        self._history.append(True)
        if self._state == "half_open":
            self._state = "closed"
            self._history.clear()

    def record_failure(self) -> None:
        self._history.append(False)
        failures = sum(1 for ok in self._history if not ok)
        rate = failures / len(self._history) if self._history else 0.0
        if self._state == "half_open" or (
            self._state == "closed"
            and len(self._history) >= self.window_size
            and rate >= self.failure_threshold
        ):
            self._state = "open"
            self._open_at = asyncio.get_event_loop().time()
            self.trips += 1


async def process(record: object, resources: dict) -> None:
    semaphore: asyncio.Semaphore = resources["api_semaphore"]
    cb: CircuitBreaker | None    = resources.get("circuit_breaker")
    mean_ms: float               = resources.get("api_mean_ms", 100.0)
    sigma: float                 = resources.get("api_sigma", 0.8)
    timeout_ms: float            = resources.get("api_timeout_ms", 500.0)
    max_retries: int             = resources.get("api_max_retries", 1)
    stats: dict                  = resources.get("api_stats")

    # Circuit breaker fast-fail
    if cb is not None and not cb.allow_request():
        record.context[f"api_{resources['leaf_idx']}"] = {
            "status": "circuit_open", "exit_ns": time.monotonic_ns()
        }
        if stats is not None:
            stats["fast_fails"] += 1
        return

    timed_out = False
    latency_ms = 0.0

    async with semaphore:
        for attempt in range(max_retries + 1):
            latency_ms = _lognormal_ms(mean_ms, sigma)
            if latency_ms <= timeout_ms:
                await asyncio.sleep(latency_ms / 1000.0)
                timed_out = False
                break
            await asyncio.sleep(timeout_ms / 1000.0)
            timed_out = True
            if stats is not None:
                stats["timeouts"] += 1
                if attempt < max_retries:
                    stats["retries"] += 1

    if cb is not None:
        if timed_out:
            cb.record_failure()
        else:
            cb.record_success()

    if stats is not None:
        stats["calls"] += 1
        stats["latency_ms"].append(latency_ms)

    record.context[f"api_{resources['leaf_idx']}"] = {
        "status":     "timeout" if timed_out else "ok",
        "latency_ms": round(latency_ms, 2),
        "cb_state":   cb.state if cb else "n/a",
        "exit_ns":    time.monotonic_ns(),
    }
