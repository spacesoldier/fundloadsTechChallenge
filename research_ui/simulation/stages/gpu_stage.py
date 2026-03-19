"""
GPU stage: puts record into a shared batch queue, awaits completion event.
The actual batch processing runs as a background asyncio.Task (gpu_batcher)
created once per leaf process in ring_blocking_sim._leaf_async_main().
"""
from __future__ import annotations
import asyncio
import time


async def process(record: object, resources: dict) -> None:
    batch_queue: asyncio.Queue = resources["gpu_batch_queue"]
    done_event = asyncio.Event()
    enqueue_ns = time.monotonic_ns()
    await batch_queue.put((record, done_event, enqueue_ns))
    await done_event.wait()
    record.context[f"gpu_{resources['leaf_idx']}"] = {
        "exit_ns": time.monotonic_ns()
    }


# ---------------------------------------------------------------------------
# Background batcher — instantiated once per leaf, runs for its lifetime
# ---------------------------------------------------------------------------

async def run_batcher(resources: dict) -> None:
    """
    Drains gpu_batch_queue in batches, simulates batch GPU execution,
    signals done_event for each record in the batch.

    Batch assembly strategy (from resources):
      strategy = "fixed"    — wait until batch_size items accumulated
      strategy = "timeout"  — flush after batch_timeout_ms or when batch_size reached
      strategy = "adaptive" — size = min(queue_depth + 1, max_batch_size), flush immediately
    """
    batch_queue: asyncio.Queue = resources["gpu_batch_queue"]
    strategy: str              = resources.get("gpu_strategy", "timeout")
    max_size: int              = resources.get("gpu_batch_size", 8)
    timeout_ms: float          = resources.get("gpu_batch_timeout_ms", 50.0)
    mean_ms: float             = resources.get("gpu_mean_ms", 50.0)
    efficiency: dict           = resources.get("gpu_efficiency", {
        1: 1.00, 2: 0.60, 4: 0.40, 8: 0.25, 16: 0.18, 32: 0.15,
    })
    stats: dict                = resources.get("gpu_stats")

    def _eff(n: int) -> float:
        # Linear interpolation between known breakpoints
        keys = sorted(efficiency.keys())
        if n <= keys[0]:
            return efficiency[keys[0]]
        if n >= keys[-1]:
            return efficiency[keys[-1]]
        for i in range(len(keys) - 1):
            lo, hi = keys[i], keys[i + 1]
            if lo <= n <= hi:
                t = (n - lo) / (hi - lo)
                return efficiency[lo] * (1 - t) + efficiency[hi] * t
        return 1.0

    loop = asyncio.get_event_loop()

    while True:
        # Wait for the first item (blocks until data arrives)
        first = await batch_queue.get()
        batch = [first]

        if strategy == "fixed":
            while len(batch) < max_size:
                item = await batch_queue.get()
                batch.append(item)

        elif strategy == "timeout":
            deadline = loop.time() + timeout_ms / 1000.0
            while len(batch) < max_size:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(batch_queue.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

        elif strategy == "adaptive":
            # Drain everything already in the queue up to max_size
            while len(batch) < max_size and not batch_queue.empty():
                batch.append(batch_queue.get_nowait())

        # Simulate batch GPU execution
        n = len(batch)
        eff = _eff(n)
        batch_ms = mean_ms * n * eff
        await asyncio.sleep(batch_ms / 1000.0)

        # Record wait times and signal done
        now_ns = time.monotonic_ns()
        if stats is not None:
            for _, _, enqueue_ns in batch:
                stats["wait_ms"].append((now_ns - enqueue_ns) / 1e6)
            stats["batches"] += 1
            stats["batch_sizes"].append(n)

        for record, done_event, _ in batch:
            done_event.set()
