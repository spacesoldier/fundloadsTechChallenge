"""
Pipeline topology simulation.

Models the real stream_kernel execution pattern: an orchestrated chain of
processing stages where root acts as a central router between all nodes.

Topology:
    [Source] ──pipe──> [Root Router] ──pipe──> [Stage-0]
                              ↑ (result)              ↓
                       [Root Router] ──pipe──> [Stage-1]
                              ↑ (result)              ↓
                              ...                     ...
                       [Root Router] ──pipe──> [Sink]
       All stages also emit ObsEvents → Root → [OBS leaf]

Key questions answered:
  - How much latency does each root-mediated routing hop add?
  - Does pipeline depth linearly increase E2E latency?
  - What happens when a middle stage is bottlenecked?
  - Does OBS traffic through root measurably impact data pipeline latency?
  - How does sync-blocking CPU work in stages affect pipeline timing?

All IPC via PipeExecutionIpcTransportAdapter from stream_kernel.
Results written to Postgres (pipeline_sim_runs/stages/hops) and Redis
(stream_kernel:pipeline_sim key prefix).

Run:
    python research_ui/simulation/pipeline_sim.py
    python research_ui/simulation/pipeline_sim.py 2   # only 2-stage scenarios
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Project path setup
# ---------------------------------------------------------------------------

_HERE         = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]          # research_ui/simulation/ → research_ui/ → project root
_SRC          = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (   # noqa: E402
    PipeExecutionIpcTransportAdapter,
)
from sim_redis import PipelineSimPublisher, register_pipeline_run, finalize_pipeline_run  # noqa: E402
from sim_postgres import write_pipeline_run                                               # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    n_stages:             int   = 2       # transform stages between source and sink
    source_rate_per_s:    float = 200.0   # records/s emitted by source
    node_latency_ms:      float = 1.0     # async sleep per stage per record
    sync_block_ms:        float = 0.0     # time.sleep() per record per stage (CPU work)
    tick_interval_ms:     float = 5.0     # scheduler tick cadence (all processes)
    ipc_poll_interval_ms: float = 5.0     # PipeExecutionIpcTransportAdapter poll interval
    drain_budget:         int   = 32      # max records drained per tick per endpoint
    obs_every_n:          int   = 0       # emit OBS event every N processed records (0=off)
    duration_s:           float = 5.0     # source emission window
    drain_extra_s:        float = 3.0     # extra time for in-flight records to drain
    warmup_s:             float = 0.3     # warmup to exclude from latency stats


# ---------------------------------------------------------------------------
# Message types (travel through OS pipes between processes)
# ---------------------------------------------------------------------------

@dataclass
class PipelineRecord:
    """Carries data through the pipeline from source to sink via root routing."""
    record_id:      int
    created_ns:     int           # set by source at emission time
    stage_exits_ns: list[int]     # appended by each stage after processing completes
    target_stage:   int = 0       # root uses this to decide next destination


@dataclass
class ObsEvent:
    """Emitted by stages → root → OBS leaf. Models observability traffic."""
    stage_name: str
    event:      str
    ts_ns:      int
    count:      int = 0


# ---------------------------------------------------------------------------
# Result stats (returned to root via multiprocessing.Queue)
# ---------------------------------------------------------------------------

@dataclass
class SourceStats:
    pid:  int = 0
    sent: int = 0


@dataclass
class StageStats:
    stage_idx:       int        = 0
    pid:             int        = 0
    received:        int        = 0
    processed:       int        = 0
    tick_actual_ms:  list[float] = field(default_factory=list)
    runner_q_depths: list[int]   = field(default_factory=list)
    obs_sent:        int        = 0


@dataclass
class SinkStats:
    pid:             int               = 0
    completed:       int               = 0
    e2e_latencies_ms: list[float]      = field(default_factory=list)
    per_hop_ms:      list[list[float]] = field(default_factory=list)
    # per_hop_ms[i] = durations from previous exit to stage i exit
    # hop 0: source.created_ns → stage_exits_ns[0]  (includes source→root + root→stage-0 routing)
    # hop i: stage_exits_ns[i-1] → stage_exits_ns[i] (includes stage→root + root→stage routing)


@dataclass
class ObsStats:
    pid:      int = 0
    received: int = 0


@dataclass
class RootStats:
    pid:          int        = 0
    routed_data:  int        = 0
    routed_obs:   int        = 0
    tick_actual_ms: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source process — emits PipelineRecords at fixed rate
# ---------------------------------------------------------------------------

def source_main(
    root_conn:  object,
    cfg:        PipelineConfig,
    result_q:   "mp.Queue",
    run_id:     str,
) -> None:
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(root_conn, target_id="root")

    stats     = SourceStats(pid=os.getpid())
    interval  = 1.0 / cfg.source_rate_per_s
    t_end     = time.monotonic() + cfg.duration_s
    t_next    = time.monotonic()
    record_id = 0

    while time.monotonic() < t_end:
        now = time.monotonic()
        if now >= t_next:
            record = PipelineRecord(
                record_id=record_id,
                created_ns=time.monotonic_ns(),
                stage_exits_ns=[],
                target_stage=0,
            )
            try:
                adapter.send("root", record, no_reply=True)
                stats.sent += 1
            except Exception:
                pass
            record_id += 1
            t_next += interval
        else:
            time.sleep(min(0.001, t_next - now))

    adapter.close()
    result_q.put(stats)


# ---------------------------------------------------------------------------
# Stage process — receives records from root, processes, returns to root
# ---------------------------------------------------------------------------

def stage_main(
    stage_idx: int,
    root_conn: object,
    cfg:       PipelineConfig,
    result_q:  "mp.Queue",
    run_id:    str,
) -> None:
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(root_conn, target_id="root")

    stats = StageStats(stage_idx=stage_idx, pid=os.getpid())
    pub   = PipelineSimPublisher(
        run_id=run_id,
        process_id=f"stage:{stage_idx}:sim:w{stage_idx}",
        process_role="stage",
        worker_index=stage_idx,
    )
    pub.connect()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                _stage_async_main(adapter, stage_idx, cfg, stats, pub),
                timeout=cfg.duration_s + cfg.drain_extra_s + 2.0,
            )
        )
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        loop.close()
        adapter.close()

    pub.flush()
    result_q.put(stats)


async def _stage_async_main(
    adapter:   PipeExecutionIpcTransportAdapter,
    stage_idx: int,
    cfg:       PipelineConfig,
    stats:     StageStats,
    pub:       PipelineSimPublisher,
) -> None:
    runner_q: asyncio.Queue[PipelineRecord] = asyncio.Queue()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)
    await asyncio.gather(
        _stage_scheduler_pump(adapter, runner_q, cfg, stats, stop),
        _stage_runner_loop(adapter, stage_idx, runner_q, cfg, stats, pub, stop),
    )


async def _stage_scheduler_pump(
    adapter:  PipeExecutionIpcTransportAdapter,
    runner_q: asyncio.Queue,
    cfg:      PipelineConfig,
    stats:    StageStats,
    stop:     asyncio.Event,
) -> None:
    last_tick = asyncio.get_event_loop().time()
    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        now = asyncio.get_event_loop().time()
        stats.tick_actual_ms.append((now - last_tick) * 1000.0)
        last_tick = now

        drained = 0
        while drained < cfg.drain_budget:
            msg = adapter.recv_buffered("root", timeout=0.0)
            if msg is None:
                break
            if isinstance(msg.payload, PipelineRecord):
                stats.received += 1
                await runner_q.put(msg.payload)
                drained += 1
        stats.runner_q_depths.append(runner_q.qsize())


async def _stage_runner_loop(
    adapter:   PipeExecutionIpcTransportAdapter,
    stage_idx: int,
    runner_q:  asyncio.Queue,
    cfg:       PipelineConfig,
    stats:     StageStats,
    pub:       PipelineSimPublisher,
    stop:      asyncio.Event,
) -> None:
    _SAMPLE_EVERY = 20
    while not stop.is_set() or not runner_q.empty():
        try:
            record: PipelineRecord = runner_q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.001)
            continue

        # Simulate node processing
        if cfg.node_latency_ms > 0:
            await asyncio.sleep(cfg.node_latency_ms / 1000.0)
        if cfg.sync_block_ms > 0:
            time.sleep(cfg.sync_block_ms / 1000.0)

        # Mark stage completion and advance routing target
        record.stage_exits_ns.append(time.monotonic_ns())
        record.target_stage = stage_idx + 1

        try:
            adapter.send("root", record, no_reply=True)
        except Exception:
            pass
        stats.processed += 1

        # Emit OBS event to root every N records
        if cfg.obs_every_n > 0 and stats.processed % cfg.obs_every_n == 0:
            obs = ObsEvent(
                stage_name=f"stage:{stage_idx}",
                event="processed",
                ts_ns=time.monotonic_ns(),
                count=stats.processed,
            )
            try:
                adapter.send("root", obs, no_reply=True)
                stats.obs_sent += 1
            except Exception:
                pass

        # Sample to Redis every N records
        if stats.processed % _SAMPLE_EVERY == 0:
            pub.publish(f"stage.{stage_idx}.tick", {
                "stage":     stage_idx,
                "processed": stats.processed,
                "runner_q":  runner_q.qsize(),
            })


# ---------------------------------------------------------------------------
# Sink process — receives completed records, records E2E latency
# ---------------------------------------------------------------------------

def sink_main(
    root_conn: object,
    cfg:       PipelineConfig,
    result_q:  "mp.Queue",
    run_id:    str,
) -> None:
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(root_conn, target_id="root")

    stats = SinkStats(pid=os.getpid())
    for _ in range(cfg.n_stages):         # pre-allocate per-hop lists
        stats.per_hop_ms.append([])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                _sink_async_main(adapter, cfg, stats),
                timeout=cfg.duration_s + cfg.drain_extra_s + 2.0,
            )
        )
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        loop.close()
        adapter.close()

    result_q.put(stats)


async def _sink_async_main(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg:     PipelineConfig,
    stats:   SinkStats,
) -> None:
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        while True:
            msg = adapter.recv_buffered("root", timeout=0.0)
            if msg is None:
                break
            if not isinstance(msg.payload, PipelineRecord):
                continue
            record = msg.payload
            now_ns = time.monotonic_ns()

            stats.e2e_latencies_ms.append((now_ns - record.created_ns) / 1_000_000.0)
            stats.completed += 1

            # Compute per-hop latencies (one hop per stage)
            if len(record.stage_exits_ns) == cfg.n_stages:
                prev_ns = record.created_ns
                for i, exit_ns in enumerate(record.stage_exits_ns):
                    if i < len(stats.per_hop_ms):
                        stats.per_hop_ms[i].append((exit_ns - prev_ns) / 1_000_000.0)
                    prev_ns = exit_ns


# ---------------------------------------------------------------------------
# OBS leaf process — receives observability events from all stages via root
# ---------------------------------------------------------------------------

def obs_main(
    root_conn: object,
    cfg:       PipelineConfig,
    result_q:  "mp.Queue",
    run_id:    str,
) -> None:
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    adapter.attach_endpoint(root_conn, target_id="root")

    stats = ObsStats(pid=os.getpid())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                _obs_async_main(adapter, cfg, stats),
                timeout=cfg.duration_s + cfg.drain_extra_s + 2.0,
            )
        )
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        loop.close()
        adapter.close()

    result_q.put(stats)


async def _obs_async_main(
    adapter: PipeExecutionIpcTransportAdapter,
    cfg:     PipelineConfig,
    stats:   ObsStats,
) -> None:
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        while True:
            msg = adapter.recv_buffered("root", timeout=0.0)
            if msg is None:
                break
            if isinstance(msg.payload, ObsEvent):
                stats.received += 1


# ---------------------------------------------------------------------------
# Root routing loop (runs in main process asyncio event loop)
# ---------------------------------------------------------------------------

_ROOT_REDIS_SAMPLE_EVERY = 20  # ticks between root Redis samples


async def _root_routing_loop(
    adapter:    PipeExecutionIpcTransportAdapter,
    cfg:        PipelineConfig,
    stats:      RootStats,
    stop:       asyncio.Event,
    pub:        PipelineSimPublisher,
) -> None:
    last_tick   = asyncio.get_event_loop().time()
    tick_count  = 0

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        now = asyncio.get_event_loop().time()
        stats.tick_actual_ms.append((now - last_tick) * 1000.0)
        last_tick  = now
        tick_count += 1

        # 1. Drain from source → forward to stage:0
        for _ in range(cfg.drain_budget):
            msg = adapter.recv_buffered("source", timeout=0.0)
            if msg is None:
                break
            if isinstance(msg.payload, PipelineRecord):
                try:
                    adapter.send("stage:0", msg.payload, no_reply=True)
                    stats.routed_data += 1
                except Exception:
                    pass

        # 2. Drain from each stage → route to next stage or sink
        for i in range(cfg.n_stages):
            for _ in range(cfg.drain_budget):
                msg = adapter.recv_buffered(f"stage:{i}", timeout=0.0)
                if msg is None:
                    break
                payload = msg.payload
                if isinstance(payload, PipelineRecord):
                    dest = f"stage:{payload.target_stage}" if payload.target_stage < cfg.n_stages else "sink"
                    try:
                        adapter.send(dest, payload, no_reply=True)
                        stats.routed_data += 1
                    except Exception:
                        pass
                elif isinstance(payload, ObsEvent):
                    try:
                        adapter.send("obs", payload, no_reply=True)
                        stats.routed_obs += 1
                    except Exception:
                        pass

        # 3. Sample root metrics to Redis
        if tick_count % _ROOT_REDIS_SAMPLE_EVERY == 0:
            pub.publish("root.routing.tick", {
                "tick_n":       tick_count,
                "tick_ms":      round(stats.tick_actual_ms[-1], 2),
                "routed_data":  stats.routed_data,
                "routed_obs":   stats.routed_obs,
            })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    return s[min(int(len(s) * p), len(s) - 1)]


def _make_run_id(label: str) -> str:
    import re
    ts   = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower())[:40].strip("-")
    return f"pipeline:{ts}:{slug}"


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_pipeline_scenario(label: str, cfg: PipelineConfig) -> None:
    ctx      = mp.get_context("fork")
    result_q: mp.Queue = ctx.Queue()
    run_id   = _make_run_id(label)

    registered = register_pipeline_run(run_id, label)

    # --- Create all pipes before fork ---
    source_parent, source_child = ctx.Pipe(duplex=True)

    stage_pipes: list[tuple] = []
    for _ in range(cfg.n_stages):
        stage_pipes.append(ctx.Pipe(duplex=True))

    sink_parent, sink_child = ctx.Pipe(duplex=True)
    obs_parent,  obs_child  = ctx.Pipe(duplex=True)

    # --- Fork all processes ---
    procs: list[mp.Process] = []

    source_proc = ctx.Process(
        target=source_main,
        args=(source_child, cfg, result_q, run_id),
        name="pipeline-source", daemon=True,
    )
    source_proc.start()
    procs.append(source_proc)

    for i, (_, stage_child) in enumerate(stage_pipes):
        proc = ctx.Process(
            target=stage_main,
            args=(i, stage_child, cfg, result_q, run_id),
            name=f"pipeline-stage-{i}", daemon=True,
        )
        proc.start()
        procs.append(proc)

    sink_proc = ctx.Process(
        target=sink_main,
        args=(sink_child, cfg, result_q, run_id),
        name="pipeline-sink", daemon=True,
    )
    sink_proc.start()
    procs.append(sink_proc)

    obs_proc = ctx.Process(
        target=obs_main,
        args=(obs_child, cfg, result_q, run_id),
        name="pipeline-obs", daemon=True,
    )
    obs_proc.start()
    procs.append(obs_proc)

    # Close child ends in parent immediately after fork
    source_child.close()
    for _, stage_child in stage_pipes:
        stage_child.close()
    sink_child.close()
    obs_child.close()

    # --- Build root adapter with all endpoints ---
    root_adapter = PipeExecutionIpcTransportAdapter(
        context=ctx,
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    root_adapter.attach_endpoint(source_parent, target_id="source")
    for i, (stage_parent, _) in enumerate(stage_pipes):
        root_adapter.attach_endpoint(stage_parent, target_id=f"stage:{i}")
    root_adapter.attach_endpoint(sink_parent, target_id="sink")
    root_adapter.attach_endpoint(obs_parent,  target_id="obs")

    # --- Root Redis publisher ---
    root_pub = PipelineSimPublisher(
        run_id=run_id, process_id="root:pipeline:w0",
        process_role="root", worker_index=0,
    )
    root_pub.connect()

    # Wait for all child processes to start their background reader threads
    time.sleep(0.15)

    # --- Run root routing loop ---
    root_stats   = RootStats(pid=os.getpid())
    stop         = asyncio.Event()
    total_runtime = cfg.duration_s + cfg.drain_extra_s

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run_root() -> None:
        asyncio.get_event_loop().call_later(total_runtime, stop.set)
        await _root_routing_loop(root_adapter, cfg, root_stats, stop, root_pub)

    try:
        loop.run_until_complete(asyncio.wait_for(_run_root(), timeout=total_runtime + 2.0))
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        loop.close()
        root_adapter.close()

    # --- Collect results: drain result_q FIRST, then kill processes ---
    # (prevents multiprocessing.Queue feeder-thread deadlock)
    total_procs = 1 + cfg.n_stages + 2   # source + stages + sink + obs
    all_results: list[object] = []
    drain_deadline = time.monotonic() + 5.0
    while len(all_results) < total_procs and time.monotonic() < drain_deadline:
        try:
            all_results.append(result_q.get(timeout=0.5))
        except Exception:
            pass

    for proc in procs:
        proc.join(timeout=2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1.0)

    # --- Classify results ---
    source_stats = next((r for r in all_results if isinstance(r, SourceStats)), SourceStats())
    stage_stats_list: list[StageStats] = sorted(
        [r for r in all_results if isinstance(r, StageStats)], key=lambda s: s.stage_idx,
    )
    sink_stats = next((r for r in all_results if isinstance(r, SinkStats)), SinkStats())
    obs_stats  = next((r for r in all_results if isinstance(r, ObsStats)),  ObsStats())

    # --- Finalize Redis ---
    total_events = source_stats.sent + root_stats.routed_data + root_stats.routed_obs
    root_pub.close(total_events=total_events)
    if registered:
        finalize_pipeline_run(run_id, total_events)

    # --- Write to Postgres ---
    _write_to_postgres(run_id, label, cfg, source_stats, stage_stats_list, sink_stats, obs_stats, root_stats)

    # --- Print report ---
    print_pipeline_report(label, cfg, source_stats, stage_stats_list, sink_stats, obs_stats, root_stats)


# ---------------------------------------------------------------------------
# Postgres writer
# ---------------------------------------------------------------------------

def _write_to_postgres(
    run_id:           str,
    label:            str,
    cfg:              PipelineConfig,
    source_stats:     SourceStats,
    stage_stats_list: list[StageStats],
    sink_stats:       SinkStats,
    obs_stats:        ObsStats,
    root_stats:       RootStats,
) -> None:
    warmup_skip = int(len(sink_stats.e2e_latencies_ms) * (cfg.warmup_s / max(cfg.duration_s, 1.0)))
    e2e    = sorted(sink_stats.e2e_latencies_ms[warmup_skip:])
    r_tick = root_stats.tick_actual_ms
    dur    = max(cfg.duration_s - cfg.warmup_s, 0.1)

    stage_rows = [
        {
            "stage_idx":  s.stage_idx,
            "pid":        s.pid,
            "received":   s.received,
            "processed":  s.processed,
            "tick_p50_ms": _pct(sorted(s.tick_actual_ms), 0.50) if s.tick_actual_ms else None,
            "runner_q_max": max(s.runner_q_depths) if s.runner_q_depths else None,
            "obs_sent":   s.obs_sent,
        }
        for s in stage_stats_list
    ]

    hop_rows = []
    for i, hop_data in enumerate(sink_stats.per_hop_ms):
        if hop_data:
            hd = sorted(hop_data)
            hop_rows.append({
                "hop_idx": i,
                "p50_ms":  _pct(hd, 0.50),
                "p90_ms":  _pct(hd, 0.90),
                "p99_ms":  _pct(hd, 0.99),
                "max_ms":  max(hd),
                "count":   len(hd),
            })

    write_pipeline_run(
        run_id=run_id, label=label,
        n_stages=cfg.n_stages,
        source_rate_per_s=cfg.source_rate_per_s,
        node_latency_ms=cfg.node_latency_ms,
        sync_block_ms=cfg.sync_block_ms,
        tick_interval_ms=cfg.tick_interval_ms,
        ipc_poll_ms=cfg.ipc_poll_interval_ms,
        obs_every_n=cfg.obs_every_n,
        duration_s=cfg.duration_s,
        total_sent=source_stats.sent,
        total_completed=sink_stats.completed,
        throughput_per_s=round(sink_stats.completed / dur, 1),
        efficiency_pct=round(sink_stats.completed / max(1, source_stats.sent) * 100, 1),
        e2e_p50_ms=_pct(e2e, 0.50) or None,
        e2e_p90_ms=_pct(e2e, 0.90) or None,
        e2e_p99_ms=_pct(e2e, 0.99) or None,
        e2e_max_ms=max(e2e) if e2e else None,
        root_tick_p50_ms=_pct(sorted(r_tick), 0.50) if r_tick else None,
        root_tick_p99_ms=_pct(sorted(r_tick), 0.99) if r_tick else None,
        root_routed_data=root_stats.routed_data,
        root_routed_obs=root_stats.routed_obs,
        obs_received=obs_stats.received,
        stages=stage_rows,
        hops=hop_rows,
    )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_pipeline_report(
    label:            str,
    cfg:              PipelineConfig,
    source_stats:     SourceStats,
    stage_stats_list: list[StageStats],
    sink_stats:       SinkStats,
    obs_stats:        ObsStats,
    root_stats:       RootStats,
) -> None:
    warmup_skip = int(len(sink_stats.e2e_latencies_ms) * (cfg.warmup_s / max(cfg.duration_s, 1.0)))
    e2e    = sorted(sink_stats.e2e_latencies_ms[warmup_skip:])
    dur    = max(cfg.duration_s - cfg.warmup_s, 0.1)
    r_tick = sorted(root_stats.tick_actual_ms)

    # Theoretical E2E floor: each hop = ipc_poll + tick + node, root adds one routing tick per hop
    # e2e_floor ≈ n_stages × (ipc_poll + tick + node) + (n_stages + 1) × root_tick
    e2e_floor = (cfg.n_stages * (cfg.ipc_poll_interval_ms + cfg.tick_interval_ms + cfg.node_latency_ms)
                 + (cfg.n_stages + 1) * cfg.tick_interval_ms)

    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)
    print(
        f"  n_stages={cfg.n_stages}  rate={cfg.source_rate_per_s:.0f}/s"
        f"  node={cfg.node_latency_ms:.1f}ms  tick={cfg.tick_interval_ms:.0f}ms"
        f"  ipc_poll={cfg.ipc_poll_interval_ms:.0f}ms"
        + (f"  sync={cfg.sync_block_ms:.1f}ms" if cfg.sync_block_ms else "")
        + (f"  obs_every={cfg.obs_every_n}" if cfg.obs_every_n else "")
    )
    print(f"  E2E floor (theoretical): ~{e2e_floor:.0f}ms")
    print(
        f"  PIDs: root={root_stats.pid}  source={source_stats.pid}"
        f"  sink={sink_stats.pid}  obs={obs_stats.pid}"
    )
    print()

    tp = sink_stats.completed / dur
    print("  ── Throughput ──────────────────────────────────────────────────")
    print(f"  Source sent:       {source_stats.sent}")
    print(f"  Sink completed:    {sink_stats.completed}  ({tp:.1f}/s)")
    print(f"  Efficiency:        {sink_stats.completed / max(1, source_stats.sent) * 100:.1f}%")
    print(f"  Root routed:       {root_stats.routed_data} data + {root_stats.routed_obs} obs")
    if obs_stats.received > 0:
        total_obs_sent = sum(s.obs_sent for s in stage_stats_list)
        print(f"  OBS: stages_sent={total_obs_sent}  root_forwarded={root_stats.routed_obs}  obs_received={obs_stats.received}")

    if e2e:
        print()
        print("  ── E2E latency (source stamp → sink receipt) ───────────────────")
        print(f"    P50: {_pct(e2e, 0.50):.1f}ms   (floor ~{e2e_floor:.0f}ms)")
        print(f"    P90: {_pct(e2e, 0.90):.1f}ms")
        print(f"    P99: {_pct(e2e, 0.99):.1f}ms")
        print(f"    max: {max(e2e):.1f}ms")

    if sink_stats.per_hop_ms and any(sink_stats.per_hop_ms):
        print()
        print("  ── Per-hop latency (source→stage-i + stage-i processing) ───────")
        prev = "source"
        for i, hop in enumerate(sink_stats.per_hop_ms):
            if hop:
                hd = sorted(hop)
                print(
                    f"  hop {i}  {prev:10s} → stage:{i} → root:"
                    f"  P50={_pct(hd, 0.50):.1f}ms  P90={_pct(hd, 0.90):.1f}ms"
                    f"  P99={_pct(hd, 0.99):.1f}ms"
                )
            prev = f"stage:{i}"

    if r_tick:
        print()
        print(f"  ── Root router tick (target={cfg.tick_interval_ms:.0f}ms) ──────────────────────────")
        print(f"    P50={_pct(r_tick, 0.50):.2f}ms  P99={_pct(r_tick, 0.99):.2f}ms  fired={len(r_tick)}")

    print()
    print("  ── Per stage ───────────────────────────────────────────────────")
    for s in stage_stats_list:
        ticks = sorted(s.tick_actual_ms) if s.tick_actual_ms else []
        rq    = s.runner_q_depths
        print(
            f"  stage:{s.stage_idx} (pid={s.pid}):"
            f"  recv={s.received}  proc={s.processed}"
            f"  tick_P50={_pct(ticks, 0.50):.2f}ms"
            f"  runner_q_max={max(rq) if rq else 0}"
            + (f"  obs_sent={s.obs_sent}" if s.obs_sent else "")
        )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("Pipeline topology simulation")
    print("Source → Root → Stage-0 → ... → Stage-N → Sink  +  OBS leaf")
    print()

    # PL-1: Baseline — 2-stage healthy pipeline
    run_pipeline_scenario(
        "PL-1: Baseline  (2 stages, 200/s, node=1ms, tick=5ms)",
        PipelineConfig(n_stages=2, source_rate_per_s=200.0, node_latency_ms=1.0,
                       tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0),
    )

    # PL-2: Deeper pipeline — 4 stages, quantify routing overhead accumulation
    run_pipeline_scenario(
        "PL-2: Deep pipeline  (4 stages, 200/s, node=1ms, tick=5ms)",
        PipelineConfig(n_stages=4, source_rate_per_s=200.0, node_latency_ms=1.0,
                       tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0),
    )

    # PL-3: Slow stages — node latency near saturation point per stage (8ms @ 100/s = 80% utilization)
    run_pipeline_scenario(
        "PL-3: Slow stages  (2 stages, 100/s, node=8ms — ~80% utilization per stage)",
        PipelineConfig(n_stages=2, source_rate_per_s=100.0, node_latency_ms=8.0,
                       tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0),
    )

    # PL-4: OBS traffic — same as PL-1 but heavy obs (every 5 records per stage)
    run_pipeline_scenario(
        "PL-4: OBS traffic  (2 stages, 200/s, node=1ms, obs_every=5)",
        PipelineConfig(n_stages=2, source_rate_per_s=200.0, node_latency_ms=1.0,
                       tick_interval_ms=5.0, ipc_poll_interval_ms=5.0,
                       obs_every_n=5, duration_s=5.0),
    )

    # PL-5: Sync block — 5ms CPU work per record models debug serialization overhead
    run_pipeline_scenario(
        "PL-5: Sync block  (2 stages, 100/s, node=0.1ms, sync=5ms — debug overhead)",
        PipelineConfig(n_stages=2, source_rate_per_s=100.0, node_latency_ms=0.1,
                       sync_block_ms=5.0, tick_interval_ms=5.0, ipc_poll_interval_ms=5.0,
                       duration_s=5.0),
    )

    # PL-6: High rate — stress root router throughput
    run_pipeline_scenario(
        "PL-6: High rate  (2 stages, 1000/s, node=0.5ms — root routing stress)",
        PipelineConfig(n_stages=2, source_rate_per_s=1000.0, node_latency_ms=0.5,
                       tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0),
    )


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
