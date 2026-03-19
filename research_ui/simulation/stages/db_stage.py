"""DB stage: asyncio.Semaphore connection pool + lognormal query latency."""
from __future__ import annotations
import asyncio
import math
import time

import numpy as np

_rng = np.random.default_rng()


def _lognormal_ms(mean_ms: float, sigma: float) -> float:
    mu = math.log(mean_ms)
    return float(np.exp(_rng.normal(mu, sigma)))


async def process(record: object, resources: dict) -> None:
    semaphore: asyncio.Semaphore = resources["db_semaphore"]
    mean_ms: float               = resources.get("db_mean_ms", 10.0)
    sigma: float                 = resources.get("db_sigma", 0.5)
    stats: dict                  = resources.get("db_stats")

    wait_start = asyncio.get_event_loop().time()
    async with semaphore:
        wait_ms = (asyncio.get_event_loop().time() - wait_start) * 1000.0
        query_ms = _lognormal_ms(mean_ms, sigma)
        await asyncio.sleep(query_ms / 1000.0)

    if stats is not None:
        stats["queries"] += 1
        stats["wait_ms"].append(wait_ms)
        stats["query_ms"].append(query_ms)

    record.context[f"db_{resources['leaf_idx']}"] = {
        "query_ms": round(query_ms, 2),
        "wait_ms":  round(wait_ms, 2),
        "exit_ns":  time.monotonic_ns(),
    }
