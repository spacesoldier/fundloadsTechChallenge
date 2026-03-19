"""CPU-only stage: constant or jittered processing delay."""
from __future__ import annotations
import asyncio
import time


async def process(record: object, resources: dict) -> None:
    ms: float = resources.get("cpu_ms", 1.0)
    if ms > 0:
        await asyncio.sleep(ms / 1000.0)
    record.context[f"cpu_{resources['leaf_idx']}"] = {"exit_ns": time.monotonic_ns()}
