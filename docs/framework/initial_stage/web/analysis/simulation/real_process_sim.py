"""
Real multiprocess simulation using the project's PipeExecutionIpcTransportAdapter.

Architecture:
  Root OS process → PipeExecutionIpcTransportAdapter (parent end)
                      background sender thread → OS pipe ↓
  Leaf OS process ← PipeExecutionIpcTransportAdapter (child end)
                      background reader thread ← OS pipe ↑
                      asyncio scheduler pump → asyncio.Queue
                      asyncio runner loop (simulates node latency)
                      → sends stats back via multiprocessing.Queue

This is the real thing:
  - actual forked OS processes (separate GIL domains, real memory isolation)
  - real OS pipes (kernel buffer, real syscall latency)
  - the project's actual PipeExecutionIpcTransportAdapter with:
      - background reader thread polling at poll_interval_seconds
      - background sender thread with send buffer
  - asyncio event loop inside the child process

Run:
    python real_process_sim.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Project import path
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[6]  # docs/framework/initial_stage/web/analysis/simulation/ → root
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (  # noqa: E402
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (  # noqa: E402
    ExecutionIpcMessage,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class RealSimConfig:
    message_rate_per_sec: float = 200.0   # root → leaf message rate
    node_latency_ms: float = 1.0          # leaf runner simulated node latency
    tick_interval_ms: float = 5.0         # leaf scheduler pump tick
    drain_budget: int = 32                # max per tick
    duration_s: float = 4.0
    warmup_s: float = 0.3
    ipc_poll_interval_ms: float = 5.0     # adapter background reader poll
    sync_block_ms: float = 0.0            # if >0: time.sleep() (no yield) per node


# ---------------------------------------------------------------------------
# Payload sent through the pipe
# ---------------------------------------------------------------------------

@dataclass
class SimPayload:
    id: int
    created_ns: int   # time.monotonic_ns() at root


# ---------------------------------------------------------------------------
# Leaf stats returned to root
# ---------------------------------------------------------------------------

@dataclass
class LeafStats:
    received: int = 0
    processed: int = 0
    e2e_latencies_ms: list[float] = field(default_factory=list)
    runner_q_depths: list[int] = field(default_factory=list)
    tick_actual_ms: list[float] = field(default_factory=list)
    ipc_buf_depths: list[int] = field(default_factory=list)
    process_pid: int = 0


# ---------------------------------------------------------------------------
# Leaf process main (runs inside child OS process)
# ---------------------------------------------------------------------------

def leaf_main(
    child_conn: object,
    cfg: RealSimConfig,
    result_q: "mp.Queue[LeafStats]",
) -> None:
    """Entry point for the forked leaf process."""
    # Silence the inherited parent adapter if any state leaked through fork.
    # Create a completely fresh adapter and attach the child end of the pipe.
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(child_conn, target_id="root")

    stats = LeafStats(process_pid=os.getpid())

    # Run asyncio event loop in the child process.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_leaf_async_main(adapter, cfg, stats))
    finally:
        loop.close()
        adapter.close()

    result_q.put(stats)


async def _leaf_async_main(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg: RealSimConfig,
    stats: LeafStats,
) -> None:
    runner_q: asyncio.Queue[SimPayload] = asyncio.Queue()
    stop = asyncio.Event()

    asyncio.get_event_loop().call_later(cfg.duration_s, stop.set)

    await asyncio.gather(
        _leaf_scheduler_pump(adapter, runner_q, cfg, stats, stop),
        _leaf_runner_loop(runner_q, cfg, stats, stop),
    )


async def _leaf_scheduler_pump(
    adapter: PipeExecutionIpcTransportAdapter,
    runner_q: asyncio.Queue[SimPayload],
    cfg: RealSimConfig,
    stats: LeafStats,
    stop: asyncio.Event,
) -> None:
    """
    system.scheduler.tick equivalent.
    Drains adapter recv buffer → runner_q at tick_interval_ms.
    """
    last_tick = asyncio.get_event_loop().time()

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)

        now = asyncio.get_event_loop().time()
        stats.tick_actual_ms.append((now - last_tick) * 1000.0)
        last_tick = now

        drained = 0
        while drained < cfg.drain_budget:
            msg: ExecutionIpcMessage | None = adapter.recv_buffered(
                "root", timeout=0.0
            )
            if msg is None:
                break
            payload = msg.payload
            if isinstance(payload, SimPayload):
                stats.received += 1
                await runner_q.put(payload)
                drained += 1

        # Sample IPC recv buffer depth via adapter metrics
        try:
            m = adapter.metrics("root")
            stats.ipc_buf_depths.append(int(m.get("queue_depth", 0)))
        except Exception:
            pass

        stats.runner_q_depths.append(runner_q.qsize())


async def _leaf_runner_loop(
    runner_q: asyncio.Queue[SimPayload],
    cfg: RealSimConfig,
    stats: LeafStats,
    stop: asyncio.Event,
) -> None:
    """
    AsyncRunner.run_async() equivalent.
    Processes messages with simulated node latency.
    """
    while not stop.is_set() or not runner_q.empty():
        try:
            payload = runner_q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.001)
            continue

        # Async node latency (yields event loop — tick can still fire)
        if cfg.node_latency_ms > 0:
            await asyncio.sleep(cfg.node_latency_ms / 1000.0)

        # Optional sync block (no yield — starves tick, models CPU-bound work)
        if cfg.sync_block_ms > 0:
            time.sleep(cfg.sync_block_ms / 1000.0)

        completed_ns = time.monotonic_ns()
        e2e_ms = (completed_ns - payload.created_ns) / 1_000_000.0
        stats.e2e_latencies_ms.append(e2e_ms)
        stats.processed += 1


# ---------------------------------------------------------------------------
# Root process sender
# ---------------------------------------------------------------------------

def _root_send_loop(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg: RealSimConfig,
) -> int:
    """Sends messages at cfg.message_rate_per_sec. Returns total sent."""
    interval_s = 1.0 / cfg.message_rate_per_sec
    t_end = time.monotonic() + cfg.duration_s
    msg_id = 0
    sent = 0
    t_next = time.monotonic()

    while time.monotonic() < t_end:
        now = time.monotonic()
        if now >= t_next:
            payload = SimPayload(id=msg_id, created_ns=time.monotonic_ns())
            try:
                adapter.send("leaf#1", payload, no_reply=True)
                sent += 1
            except Exception:
                pass
            msg_id += 1
            t_next += interval_s
        else:
            # Busy-wait with small sleep to avoid burning CPU
            time.sleep(min(0.001, t_next - now))

    return sent


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    return s[min(int(len(s) * p), len(s) - 1)]


def print_real_report(
    label: str,
    cfg: RealSimConfig,
    sent: int,
    stats: LeafStats,
) -> None:
    duration = cfg.duration_s - cfg.warmup_s
    # Filter out warmup period from latency measurements
    # (approximate: take last (duration/cfg.duration_s) fraction of results)
    warmup_frac = cfg.warmup_s / cfg.duration_s
    skip = int(len(stats.e2e_latencies_ms) * warmup_frac)
    latencies = stats.e2e_latencies_ms[skip:]

    print()
    print("=" * 64)
    print(f"  {label}")
    print("=" * 64)
    print(
        f"  Config: rate={cfg.message_rate_per_sec:.0f}/s  "
        f"node={cfg.node_latency_ms:.1f}ms  "
        f"tick={cfg.tick_interval_ms:.1f}ms  "
        f"ipc_poll={cfg.ipc_poll_interval_ms:.0f}ms"
        + (f"  sync_block={cfg.sync_block_ms:.0f}ms" if cfg.sync_block_ms > 0 else "")
    )
    print(f"  Root PID: {os.getpid()}  Leaf PID: {stats.process_pid}")
    print()

    tp = stats.processed / duration if duration > 0 else 0
    print(f"  Messages sent:       {sent}")
    print(f"  Messages received:   {stats.received}")
    print(f"  Messages processed:  {stats.processed}  ({tp:.1f}/s)")
    print(f"  Lost in transit:     {sent - stats.received}")

    if latencies:
        print()
        print(f"  E2E latency (root stamp → leaf completion):")
        print(f"    P50:  {_pct(latencies, 0.50):.1f}ms")
        print(f"    P90:  {_pct(latencies, 0.90):.1f}ms")
        print(f"    P99:  {_pct(latencies, 0.99):.1f}ms")
        print(f"    max:  {max(latencies):.1f}ms")

    ticks = stats.tick_actual_ms[1:]  # skip first
    if ticks:
        print()
        print(f"  Scheduler tick (target={cfg.tick_interval_ms:.0f}ms):")
        print(f"    P50:  {_pct(ticks, 0.50):.2f}ms")
        print(f"    P99:  {_pct(ticks, 0.99):.2f}ms")
        print(f"    fired: {len(ticks)}")

    if stats.runner_q_depths:
        print()
        print(f"  Runner queue depth (leaf asyncio.Queue):")
        print(f"    max:  {max(stats.runner_q_depths)}")
        print(f"    mean: {sum(stats.runner_q_depths)/len(stats.runner_q_depths):.1f}")

    if stats.ipc_buf_depths:
        print(f"  IPC recv buffer depth (adapter.metrics):")
        print(f"    max:  {max(stats.ipc_buf_depths)}")
        print(f"    mean: {sum(stats.ipc_buf_depths)/len(stats.ipc_buf_depths):.1f}")


# ---------------------------------------------------------------------------
# Run one scenario
# ---------------------------------------------------------------------------

def run_scenario(label: str, cfg: RealSimConfig) -> None:
    ctx = mp.get_context("fork")

    # Create raw duplex pipe BEFORE fork (pipe is inherited by child)
    parent_conn, child_conn = ctx.Pipe(duplex=True)
    result_q: mp.Queue = ctx.Queue()

    # Fork leaf process
    proc = ctx.Process(
        target=leaf_main,
        args=(child_conn, cfg, result_q),
        name="leaf#1",
        daemon=True,
    )
    proc.start()

    # Close child end in parent (child owns it now)
    child_conn.close()

    # Root: create adapter, attach parent end
    parent_adapter = PipeExecutionIpcTransportAdapter(
        context=ctx,
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    parent_adapter.attach_endpoint(parent_conn, target_id="leaf#1")

    # Small delay for child to start its adapter's background reader
    time.sleep(0.1)

    # Send messages
    sent = _root_send_loop(parent_adapter, cfg)

    # Wait for child to finish (it stops after duration_s)
    proc.join(timeout=cfg.duration_s + 2.0)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=1.0)

    parent_adapter.close()

    # Collect stats
    try:
        stats: LeafStats = result_q.get(timeout=2.0)
    except Exception:
        stats = LeafStats()

    print_real_report(label, cfg, sent, stats)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("Real multiprocess simulation")
    print("Using PipeExecutionIpcTransportAdapter + fork + asyncio in child")
    print()

    # RS-1: Healthy baseline
    run_scenario(
        "RS-1: Healthy  (200/s, node=1ms, tick=5ms, ipc_poll=5ms)",
        RealSimConfig(
            message_rate_per_sec=200,
            node_latency_ms=1.0,
            tick_interval_ms=5.0,
            ipc_poll_interval_ms=5.0,
            duration_s=4.0,
        ),
    )

    # RS-2: Runner overloaded (node slow → runner_q fills)
    run_scenario(
        "RS-2: Runner overloaded  (200/s, node=8ms — runner backlog)",
        RealSimConfig(
            message_rate_per_sec=200,
            node_latency_ms=8.0,
            tick_interval_ms=5.0,
            ipc_poll_interval_ms=5.0,
            duration_s=4.0,
        ),
    )

    # RS-3: IPC reader slow — messages pile in adapter recv buffer
    run_scenario(
        "RS-3: Slow IPC reader  (200/s, node=1ms, ipc_poll=30ms — IPC recv backlog)",
        RealSimConfig(
            message_rate_per_sec=200,
            node_latency_ms=1.0,
            tick_interval_ms=5.0,
            ipc_poll_interval_ms=30.0,   # reader wakes every 30ms
            duration_s=4.0,
        ),
    )

    # RS-4: Sync block — no yield, tick starved (models debug serialize)
    run_scenario(
        "RS-4: Sync block  (100/s, sync_cpu=10ms — blocks event loop)",
        RealSimConfig(
            message_rate_per_sec=100,
            node_latency_ms=0.1,
            tick_interval_ms=5.0,
            ipc_poll_interval_ms=5.0,
            sync_block_ms=10.0,          # time.sleep, no yield
            duration_s=4.0,
        ),
    )

    # RS-5: Tick too coarse for throughput (fast nodes, slow tick)
    run_scenario(
        "RS-5: Coarse tick  (500/s, node=0.1ms, tick=20ms — drain lag)",
        RealSimConfig(
            message_rate_per_sec=500,
            node_latency_ms=0.1,
            tick_interval_ms=20.0,       # 20ms tick → 10 messages per tick at 500/s
            drain_budget=64,
            ipc_poll_interval_ms=5.0,
            duration_s=4.0,
        ),
    )


if __name__ == "__main__":
    # Required for multiprocessing on some platforms
    mp.set_start_method("fork", force=True)
    main()
