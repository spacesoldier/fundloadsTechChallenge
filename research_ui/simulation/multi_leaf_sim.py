"""
Multi-leaf real process simulation.

Topology:
  Root OS process
    PipeExecutionIpcTransportAdapter
      endpoint "leaf#1" → OS pipe → Leaf#1 OS process
      endpoint "leaf#2" → OS pipe → Leaf#2 OS process
      ...
      endpoint "leaf#N" → OS pipe → Leaf#N OS process

Root sends round-robin across all leaves at a fixed total rate.
Each leaf runs its own asyncio event loop (scheduler pump + runner loop).
Stats collected per-leaf via multiprocessing.Queue.

Run:
    python multi_leaf_sim.py           # default: 1, 5, 10 leaves
    python multi_leaf_sim.py 5         # just 5 leaves
    python multi_leaf_sim.py 10        # just 10 leaves
"""

from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Project import path
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_SIM_DIR = _HERE.parent
_PROJECT_ROOT = _HERE.parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (  # noqa: E402
    PipeExecutionIpcTransportAdapter,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (  # noqa: E402
    ExecutionIpcMessage,
)
from sim_redis_publisher import SimPublisher, register_run, finalize_run  # noqa: E402
from sim_postgres_writer import write_scenario  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class MultiLeafConfig:
    n_leaves: int = 5
    total_rate_per_sec: float = 1000.0   # distributed round-robin across all leaves
    node_latency_ms: float = 1.0
    tick_interval_ms: float = 5.0
    drain_budget: int = 32
    ipc_poll_interval_ms: float = 5.0
    duration_s: float = 5.0
    warmup_s: float = 0.3
    sync_block_ms: float = 0.0

    @property
    def rate_per_leaf(self) -> float:
        return self.total_rate_per_sec / self.n_leaves


# ---------------------------------------------------------------------------
# Payload / stats
# ---------------------------------------------------------------------------

@dataclass
class SimPayload:
    id: int
    leaf_id: int
    created_ns: int


@dataclass
class LeafStats:
    leaf_id: int = 0
    process_pid: int = 0
    received: int = 0
    processed: int = 0
    e2e_latencies_ms: list[float] = field(default_factory=list)
    runner_q_depths: list[int] = field(default_factory=list)
    tick_actual_ms: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Leaf process
# ---------------------------------------------------------------------------

def leaf_main(
    leaf_id: int,
    child_conn: object,
    cfg: MultiLeafConfig,
    result_q: "mp.Queue[LeafStats]",
    run_id: str,
) -> None:
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(child_conn, target_id="root")

    stats = LeafStats(leaf_id=leaf_id, process_pid=os.getpid())

    # Redis publisher — optional, silently skipped if Redis not reachable
    pub = SimPublisher(
        run_id=run_id,
        process_id=f"leaf:sim:w{leaf_id}",
        process_role="leaf",
        worker_index=leaf_id,
    )
    pub.connect()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                _leaf_async_main(adapter, cfg, stats, pub),
                timeout=cfg.duration_s + 2.0,
            )
        )
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        loop.close()
        adapter.close()

    pub.close(total_events=len(stats.tick_actual_ms))
    result_q.put(stats)


async def _leaf_async_main(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg: MultiLeafConfig,
    stats: LeafStats,
    pub: SimPublisher,
) -> None:
    runner_q: asyncio.Queue[SimPayload] = asyncio.Queue()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s, stop.set)
    await asyncio.gather(
        _leaf_scheduler_pump(adapter, runner_q, cfg, stats, stop, pub),
        _leaf_runner_loop(runner_q, cfg, stats, stop),
    )


_REDIS_SAMPLE_EVERY = 10  # publish every N-th tick to Redis

async def _leaf_scheduler_pump(
    adapter: PipeExecutionIpcTransportAdapter,
    runner_q: asyncio.Queue[SimPayload],
    cfg: MultiLeafConfig,
    stats: LeafStats,
    stop: asyncio.Event,
    pub: SimPublisher,
) -> None:
    last_tick = asyncio.get_event_loop().time()
    tick_count = 0
    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        now = asyncio.get_event_loop().time()
        tick_ms = (now - last_tick) * 1000.0
        stats.tick_actual_ms.append(tick_ms)
        last_tick = now
        tick_count += 1

        drained = 0
        while drained < cfg.drain_budget:
            msg: ExecutionIpcMessage | None = adapter.recv_buffered("root", timeout=0.0)
            if msg is None:
                break
            payload = msg.payload
            if isinstance(payload, SimPayload):
                stats.received += 1
                await runner_q.put(payload)
                drained += 1

        rq = runner_q.qsize()
        stats.runner_q_depths.append(rq)

        # Sample every N-th tick → Redis
        if tick_count % _REDIS_SAMPLE_EVERY == 0:
            pub.publish("sim.tick", {
                "leaf_id": stats.leaf_id,
                "tick_n": tick_count,
                "tick_ms": round(tick_ms, 2),
                "runner_q": rq,
                "drained": drained,
            })


async def _leaf_runner_loop(
    runner_q: asyncio.Queue[SimPayload],
    cfg: MultiLeafConfig,
    stats: LeafStats,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set() or not runner_q.empty():
        try:
            payload = runner_q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.001)
            continue

        if cfg.node_latency_ms > 0:
            await asyncio.sleep(cfg.node_latency_ms / 1000.0)
        if cfg.sync_block_ms > 0:
            time.sleep(cfg.sync_block_ms / 1000.0)

        completed_ns = time.monotonic_ns()
        e2e_ms = (completed_ns - payload.created_ns) / 1_000_000.0
        stats.e2e_latencies_ms.append(e2e_ms)
        stats.processed += 1


# ---------------------------------------------------------------------------
# Root sender — round-robin across N leaves
# ---------------------------------------------------------------------------

def _root_send_loop(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg: MultiLeafConfig,
) -> int:
    interval_s = 1.0 / cfg.total_rate_per_sec
    t_end = time.monotonic() + cfg.duration_s
    msg_id = 0
    sent = 0
    t_next = time.monotonic()
    n = cfg.n_leaves

    while time.monotonic() < t_end:
        now = time.monotonic()
        if now >= t_next:
            leaf_idx = (msg_id % n) + 1
            target = f"leaf#{leaf_idx}"
            payload = SimPayload(id=msg_id, leaf_id=leaf_idx, created_ns=time.monotonic_ns())
            try:
                adapter.send(target, payload, no_reply=True)
                sent += 1
            except Exception:
                pass
            msg_id += 1
            t_next += interval_s
        else:
            time.sleep(min(0.001, t_next - now))

    return sent


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    return s[min(int(len(s) * p), len(s) - 1)]


def _merge_stats(all_stats: list[LeafStats]) -> LeafStats:
    merged = LeafStats()
    for s in all_stats:
        merged.received += s.received
        merged.processed += s.processed
        merged.e2e_latencies_ms.extend(s.e2e_latencies_ms)
        merged.runner_q_depths.extend(s.runner_q_depths)
        merged.tick_actual_ms.extend(s.tick_actual_ms)
    return merged


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_multi_report(
    label: str,
    cfg: MultiLeafConfig,
    sent: int,
    all_stats: list[LeafStats],
    root_pid: int,
) -> None:
    merged = _merge_stats(all_stats)
    duration = cfg.duration_s - cfg.warmup_s
    warmup_frac = cfg.warmup_s / cfg.duration_s

    # Warmup-filtered latencies (global)
    skip = int(len(merged.e2e_latencies_ms) * warmup_frac)
    latencies = merged.e2e_latencies_ms[skip:]

    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)
    print(
        f"  Config: n_leaves={cfg.n_leaves}  total_rate={cfg.total_rate_per_sec:.0f}/s"
        f"  ({cfg.rate_per_leaf:.0f}/s per leaf)"
    )
    print(
        f"         node={cfg.node_latency_ms:.1f}ms  tick={cfg.tick_interval_ms:.1f}ms"
        f"  ipc_poll={cfg.ipc_poll_interval_ms:.0f}ms"
        + (f"  sync_block={cfg.sync_block_ms:.0f}ms" if cfg.sync_block_ms else "")
    )
    print(f"  Root PID: {root_pid}  |  Leaf PIDs: {[s.process_pid for s in all_stats]}")
    print()

    tp = merged.processed / duration if duration > 0 else 0
    print(f"  ── Aggregate ───────────────────────────────────────────────────")
    print(f"  Messages sent:        {sent}")
    print(f"  Messages received:    {merged.received}  (lost={sent - merged.received})")
    print(f"  Messages processed:   {merged.processed}  ({tp:.1f}/s)")
    print(f"  Throughput efficiency:{merged.processed / max(1, sent) * 100:.1f}%")

    if latencies:
        print()
        print(f"  E2E latency (root stamp → leaf completion) — aggregate:")
        print(f"    P50:  {_pct(latencies, 0.50):.1f}ms")
        print(f"    P90:  {_pct(latencies, 0.90):.1f}ms")
        print(f"    P99:  {_pct(latencies, 0.99):.1f}ms")
        print(f"    max:  {max(latencies):.1f}ms")

    all_ticks = merged.tick_actual_ms[len(merged.tick_actual_ms) // (cfg.n_leaves * 4):]
    if all_ticks:
        print()
        print(f"  Scheduler tick (target={cfg.tick_interval_ms:.0f}ms) — aggregate:")
        print(f"    P50:  {_pct(all_ticks, 0.50):.2f}ms")
        print(f"    P99:  {_pct(all_ticks, 0.99):.2f}ms")
        print(f"    total fired: {len(all_ticks)}")

    max_runner_q = max((max(s.runner_q_depths) for s in all_stats if s.runner_q_depths), default=0)
    mean_runner_q = (
        sum(sum(s.runner_q_depths) for s in all_stats)
        / max(1, sum(len(s.runner_q_depths) for s in all_stats))
    )
    print()
    print(f"  Runner queue depth — across all leaves:")
    print(f"    max (any leaf): {max_runner_q}")
    print(f"    mean (all):     {mean_runner_q:.1f}")

    print()
    print(f"  ── Per leaf ────────────────────────────────────────────────────")
    for s in all_stats:
        skip_i = int(len(s.e2e_latencies_ms) * warmup_frac)
        lat = s.e2e_latencies_ms[skip_i:]
        ticks = s.tick_actual_ms[1:]
        rq_max = max(s.runner_q_depths) if s.runner_q_depths else 0
        print(
            f"  leaf#{s.leaf_id} (pid={s.process_pid}): "
            f"recv={s.received} proc={s.processed} "
            f"| E2E P50={_pct(lat, 0.50):.1f}ms P99={_pct(lat, 0.99):.1f}ms "
            f"| tick P50={_pct(ticks, 0.50):.2f}ms "
            f"| runner_q max={rq_max}"
        )


# ---------------------------------------------------------------------------
# Run one scenario
# ---------------------------------------------------------------------------

def _make_run_id(label: str) -> str:
    import re
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower())[:40].strip("-")
    return f"sim:{ts}:{slug}"


def run_multi_scenario(label: str, cfg: MultiLeafConfig) -> None:
    ctx = mp.get_context("fork")
    result_q: mp.Queue = ctx.Queue()

    run_id = _make_run_id(label)
    registered = register_run(run_id, label)

    # Create N pipes before fork
    pipes: list[tuple[object, object]] = []
    for _ in range(cfg.n_leaves):
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        pipes.append((parent_conn, child_conn))

    # Fork N leaf processes
    procs: list[mp.Process] = []
    for i, (_, child_conn) in enumerate(pipes):
        leaf_id = i + 1
        proc = ctx.Process(
            target=leaf_main,
            args=(leaf_id, child_conn, cfg, result_q, run_id),
            name=f"leaf#{leaf_id}",
            daemon=True,
        )
        proc.start()
        procs.append(proc)

    # Close child ends in parent
    for _, child_conn in pipes:
        child_conn.close()

    # Root adapter — attach all leaf endpoints
    parent_adapter = PipeExecutionIpcTransportAdapter(
        context=ctx,
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    for i, (parent_conn, _) in enumerate(pipes):
        leaf_id = i + 1
        parent_adapter.attach_endpoint(parent_conn, target_id=f"leaf#{leaf_id}")

    # Wait for all leaves to start their background readers
    time.sleep(0.15)

    # Send messages round-robin
    sent = _root_send_loop(parent_adapter, cfg)

    # Wait for all leaves to finish.
    # Use SIGKILL (proc.kill) not SIGTERM: SIGTERM triggers finally blocks in the child
    # which call adapter.close() → thread.join() without timeout → deadlock if pipe is full.
    # IMPORTANT ordering to avoid multiprocessing.Queue deadlock:
    # When leaf puts large stats into result_q, the feeder thread writes to the OS pipe.
    # If root kills the leaf before draining the queue, the feeder thread blocks on a
    # full OS pipe and SIGKILL kills it mid-write → corrupted/incomplete data in pipe.
    # Fix: drain result_q FIRST (clears pipe, lets feeder threads finish),
    # then join/kill procs (which by now have already exited naturally).

    # Step 1: drain result_q (give leaves time to put their stats)
    all_stats: list[LeafStats] = []
    drain_deadline = time.monotonic() + cfg.duration_s + 3.0
    while len(all_stats) < cfg.n_leaves and time.monotonic() < drain_deadline:
        try:
            stats = result_q.get(timeout=0.5)
            all_stats.append(stats)
        except Exception:
            pass

    # Step 2: join procs (they should be done by now)
    for proc in procs:
        proc.join(timeout=2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1.0)

    parent_adapter.close()

    # Sort by leaf_id for consistent output
    all_stats.sort(key=lambda s: s.leaf_id)

    # Publish aggregate scenario summary to Redis from root
    if registered:
        merged = _merge_stats(all_stats)
        duration = max(cfg.duration_s - cfg.warmup_s, 0.1)
        latencies = sorted(merged.e2e_latencies_ms)
        ticks = merged.tick_actual_ms

        def _pct_local(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            return data[min(int(len(data) * p), len(data) - 1)]

        root_pub = SimPublisher(
            run_id=run_id,
            process_id="root:sim:w0",
            process_role="root",
            worker_index=0,
        )
        if root_pub.connect():
            root_pub.publish("sim.scenario.summary", {
                "label": label,
                "n_leaves": cfg.n_leaves,
                "total_rate_per_sec": cfg.total_rate_per_sec,
                "rate_per_leaf": cfg.rate_per_leaf,
                "node_latency_ms": cfg.node_latency_ms,
                "tick_interval_ms": cfg.tick_interval_ms,
                "ipc_poll_interval_ms": cfg.ipc_poll_interval_ms,
                "sync_block_ms": cfg.sync_block_ms,
                "duration_s": cfg.duration_s,
                "sent": sent,
                "received": merged.received,
                "processed": merged.processed,
                "throughput_per_s": round(merged.processed / duration, 1),
                "efficiency_pct": round(merged.processed / max(1, sent) * 100, 1),
                "e2e_p50_ms": round(_pct_local(latencies, 0.50), 2),
                "e2e_p90_ms": round(_pct_local(latencies, 0.90), 2),
                "e2e_p99_ms": round(_pct_local(latencies, 0.99), 2),
                "e2e_max_ms": round(max(latencies) if latencies else 0, 2),
                "tick_p50_ms": round(_pct_local(sorted(ticks), 0.50), 2),
                "tick_p99_ms": round(_pct_local(sorted(ticks), 0.99), 2),
                "runner_q_max": max((max(s.runner_q_depths) for s in all_stats if s.runner_q_depths), default=0),
                "leaf_pids": [s.process_pid for s in all_stats],
            })
            root_pub.close(total_events=merged.received + 1)

        total_events = merged.received + cfg.n_leaves + 1
        finalize_run(run_id, total_events)

    # Write aggregate results to postgres
    _write_to_postgres(run_id, label, cfg, sent, all_stats)

    print_multi_report(label, cfg, sent, all_stats, os.getpid())


def _write_to_postgres(
    run_id: str,
    label: str,
    cfg: MultiLeafConfig,
    sent: int,
    all_stats: list[LeafStats],
) -> None:
    merged = _merge_stats(all_stats)
    duration = max(cfg.duration_s - cfg.warmup_s, 0.1)
    warmup_frac = cfg.warmup_s / cfg.duration_s
    skip = int(len(merged.e2e_latencies_ms) * warmup_frac)
    latencies = sorted(merged.e2e_latencies_ms[skip:])
    ticks = merged.tick_actual_ms

    runner_q_all = [d for s in all_stats for d in s.runner_q_depths]

    leaf_rows = []
    for s in all_stats:
        skip_i = int(len(s.e2e_latencies_ms) * warmup_frac)
        lat = sorted(s.e2e_latencies_ms[skip_i:])
        t = s.tick_actual_ms
        leaf_rows.append({
            "leaf_id": s.leaf_id,
            "pid": s.process_pid,
            "received": s.received,
            "processed": s.processed,
            "e2e_p50_ms": _pct(lat, 0.50),
            "e2e_p99_ms": _pct(lat, 0.99),
            "tick_p50_ms": _pct(sorted(t), 0.50),
            "runner_q_max": max(s.runner_q_depths) if s.runner_q_depths else None,
        })

    write_scenario(
        run_id=run_id,
        label=label,
        sim_type="multi_leaf",
        n_leaves=cfg.n_leaves,
        total_rate_per_s=cfg.total_rate_per_sec,
        rate_per_leaf_s=cfg.rate_per_leaf,
        node_latency_ms=cfg.node_latency_ms,
        tick_interval_ms=cfg.tick_interval_ms,
        ipc_poll_ms=cfg.ipc_poll_interval_ms,
        sync_block_ms=cfg.sync_block_ms,
        duration_s=cfg.duration_s,
        sent=sent,
        received=merged.received,
        processed=merged.processed,
        throughput_per_s=round(merged.processed / duration, 1),
        efficiency_pct=round(merged.processed / max(1, sent) * 100, 1),
        e2e_p50_ms=_pct(latencies, 0.50),
        e2e_p90_ms=_pct(latencies, 0.90),
        e2e_p99_ms=_pct(latencies, 0.99),
        e2e_max_ms=max(latencies) if latencies else None,
        tick_p50_ms=_pct(sorted(ticks), 0.50),
        tick_p99_ms=_pct(sorted(ticks), 0.99),
        ticks_fired=len(ticks),
        runner_q_max=max(runner_q_all) if runner_q_all else None,
        runner_q_mean=sum(runner_q_all) / len(runner_q_all) if runner_q_all else None,
        leaves=leaf_rows,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def main() -> None:
    leaf_counts = [1, 5, 10]
    if len(sys.argv) > 1:
        try:
            leaf_counts = [int(sys.argv[1])]
        except ValueError:
            pass

    print()
    print("Multi-leaf real process simulation")
    print("Root → N leaf processes, each with own OS pipe + PipeExecutionIpcTransportAdapter")
    print("Messages distributed round-robin from root to all leaves")
    print()

    for n in leaf_counts:
        # ML-1: Healthy — 200/s per leaf, scale total rate with N
        run_multi_scenario(
            f"ML-1: Healthy  ({n} leaves × 200/s = {n*200}/s total, node=1ms, tick=5ms)",
            MultiLeafConfig(
                n_leaves=n,
                total_rate_per_sec=n * 200.0,
                node_latency_ms=1.0,
                tick_interval_ms=5.0,
                ipc_poll_interval_ms=5.0,
                duration_s=5.0,
            ),
        )

        # ML-2: Fixed total rate — see how leaves share load
        run_multi_scenario(
            f"ML-2: Fixed rate  ({n} leaves, total=1000/s = {1000//n}/s per leaf, node=1ms)",
            MultiLeafConfig(
                n_leaves=n,
                total_rate_per_sec=1000.0,
                node_latency_ms=1.0,
                tick_interval_ms=5.0,
                ipc_poll_interval_ms=5.0,
                duration_s=5.0,
            ),
        )

        # ML-3: Sync block — each leaf has 5ms sync work (models debug serialization)
        run_multi_scenario(
            f"ML-3: Sync block  ({n} leaves, total={n*100}/s, sync_cpu=5ms per msg)",
            MultiLeafConfig(
                n_leaves=n,
                total_rate_per_sec=n * 100.0,
                node_latency_ms=0.1,
                tick_interval_ms=5.0,
                ipc_poll_interval_ms=5.0,
                sync_block_ms=5.0,
                duration_s=5.0,
            ),
        )

        print()


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
