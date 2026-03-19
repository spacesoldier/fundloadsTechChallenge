"""
Multi-lane star topology simulation — Experiment A.

Same routing topology as pipeline_sim.py (star: all data passes through root)
but each process pair is connected by 5 named OS pipes instead of 1:

    data        — payload records     (root → stage; stage result → root)
    control     — ACKs/flow credits   (bidirectional; ACKs not enabled by default)
    logs        — log ObsEvents       (stage → root → obs)
    monitoring  — metric ObsEvents    (stage → root → obs)
    traces      — trace ObsEvents     (stage → root → obs)

Root adapter endpoint count:
    (n_stages + 3) × 5   [source + stages + sink + obs, each with 5 lanes]
    n_stages=2 → 25 endpoints   n_stages=4 → 35 endpoints

Key questions vs single-lane baseline (pipeline_sim.py):
  - Does root's reader loop degrade when polling 5× more endpoints?
  - Does OBS lane separation reduce head-of-line blocking on data path?
  - How does per-hop latency compare to PL-1 / PL-2?
  - At heavy OBS load (obs_every=5), do the separate OBS lanes insulate data E2E?

Scenarios:
  A-1: Baseline        — 2 stages, 200/s, node=1ms, tick=5ms  (compare to PL-1)
  A-3: Heavy OBS       — 2 stages, 200/s, node=1ms, obs_every=5 on all 3 OBS lanes
  A-4: Deep            — 4 stages, 200/s, node=1ms, tick=5ms  (compare to PL-2)
  A-5: Realistic load  — 5 stages, 200/s, 4 OBS steps/msg (12 OBS events per data msg
                          per stage), ACKs enabled on control lane. Root sends 13 ACKs
                          per data message per stage; OBS leaf sends 60 ACKs/msg to root.

Results written to Postgres (lane_sim_runs/stages/hops) and Redis
(stream_kernel:lane_sim key prefix).

Run:
    python research_ui/simulation/multi_lane_star_sim.py
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
_PROJECT_ROOT = _HERE.parents[2]      # simulation/ → research_ui/ → project root
_SRC          = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import (   # noqa: E402
    PipeExecutionIpcTransportAdapter,
)
from lane_utils import LanedPipeSet                                          # noqa: E402
from sim_redis import PipelineSimPublisher, register_pipeline_run, finalize_pipeline_run  # noqa: E402
from lane_sim_postgres import write_lane_run                                 # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class LaneStarConfig:
    n_stages:             int   = 2       # transform stages between source and sink
    source_rate_per_s:    float = 200.0   # records/s emitted by source
    node_latency_ms:      float = 1.0     # async sleep per stage per record
    sync_block_ms:        float = 0.0     # time.sleep() per record per stage (CPU work)
    tick_interval_ms:     float = 5.0     # scheduler tick cadence (all processes)
    ipc_poll_interval_ms: float = 5.0     # PipeExecutionIpcTransportAdapter poll interval
    drain_budget:         int   = 32      # max records drained per tick per endpoint
    obs_every_n:          int   = 0       # emit OBS events every N records, round-robin lanes
    obs_steps_per_msg:    int   = 0       # emit N OBS events on EACH of 3 lanes per data msg
    # (obs_steps_per_msg=4 means 12 OBS events per data message per stage)
    ack_enabled:          bool  = False   # root sends ACK on control lane for each received msg
    # ACK model: root→stage:control (1 ACK per msg received); obs→root:control (1 per OBS received)
    duration_s:           float = 5.0    # source emission window
    drain_extra_s:        float = 3.0    # extra time for in-flight records to drain
    warmup_s:             float = 0.3    # warmup fraction to exclude from latency stats

    def n_endpoints(self) -> int:
        """Root's total adapter endpoint count: (stages + source + sink + obs) × 5."""
        return (self.n_stages + 3) * 5


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------

@dataclass
class PipelineRecord:
    """Carries data through the pipeline. Travels on the :data lane."""
    record_id:      int
    created_ns:     int
    stage_exits_ns: list[int]
    target_stage:   int = 0


@dataclass
class ObsEvent:
    """Observability event. Travels on :logs / :monitoring / :traces lanes."""
    stage_name: str
    lane:       str    # "logs" | "monitoring" | "traces"
    event:      str
    ts_ns:      int
    count:      int = 0


@dataclass
class AckMessage:
    """Flow-control acknowledgement. Travels on :control lane.

    Sent by root → stage:control for every message root receives from that stage.
    Sent by obs  → root:control  for every OBS event obs receives from root.
    """
    ack_count: int = 1   # number of messages this ACK covers (always 1 per-message here)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class SourceStats:
    pid:  int = 0
    sent: int = 0


@dataclass
class StageStats:
    stage_idx:          int         = 0
    pid:                int         = 0
    received:           int         = 0
    processed:          int         = 0
    tick_actual_ms:     list[float] = field(default_factory=list)
    runner_q_depths:    list[int]   = field(default_factory=list)
    obs_logs_sent:      int         = 0
    obs_mon_sent:       int         = 0
    obs_traces_sent:    int         = 0
    acks_received:      int         = 0   # ACKs from root on control lane


@dataclass
class SinkStats:
    pid:                int               = 0
    completed:          int               = 0
    e2e_latencies_ms:   list[float]       = field(default_factory=list)
    per_hop_ms:         list[list[float]] = field(default_factory=list)


@dataclass
class ObsStats:
    pid:          int = 0
    recv_logs:    int = 0
    recv_mon:     int = 0
    recv_traces:  int = 0
    acks_sent:    int = 0   # ACKs sent back to root on root:control lane

    @property
    def received(self) -> int:
        return self.recv_logs + self.recv_mon + self.recv_traces


@dataclass
class RootStats:
    pid:              int         = 0
    routed_data:      int         = 0
    routed_logs:      int         = 0
    routed_mon:       int         = 0
    routed_traces:    int         = 0
    acks_to_stages:   int         = 0   # ACKs sent by root → stage:control lanes
    acks_from_obs:    int         = 0   # ACKs received by root from obs:control lane
    tick_actual_ms:   list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source process
# ---------------------------------------------------------------------------

def source_main(
    pipe_set: LanedPipeSet,
    cfg:      LaneStarConfig,
    result_q: "mp.Queue",
    run_id:   str,
) -> None:
    # Close parent ends (held by root)
    pipe_set.close_parent_ends()

    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    # Attach all 5 child-side connections so the reader thread polls them all
    # (only data lane carries traffic, but all 5 endpoints contribute to root's
    # reader-loop load on the root side — that's what we're measuring)
    pipe_set.attach_child_to_adapter(adapter, prefix="root")

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
                adapter.send("root:data", record, no_reply=True)
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
# Stage process
# ---------------------------------------------------------------------------

def stage_main(
    stage_idx: int,
    pipe_set:  LanedPipeSet,
    cfg:       LaneStarConfig,
    result_q:  "mp.Queue",
    run_id:    str,
) -> None:
    pipe_set.close_parent_ends()

    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    pipe_set.attach_child_to_adapter(adapter, prefix="root")

    stats = StageStats(stage_idx=stage_idx, pid=os.getpid())
    pub   = PipelineSimPublisher(
        run_id=run_id,
        process_id=f"stage:{stage_idx}:lane:w{stage_idx}",
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
    cfg:       LaneStarConfig,
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
    cfg:      LaneStarConfig,
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
            msg = adapter.recv_buffered("root:data", timeout=0.0)
            if msg is None:
                break
            if isinstance(msg.payload, PipelineRecord):
                stats.received += 1
                await runner_q.put(msg.payload)
                drained += 1
        stats.runner_q_depths.append(runner_q.qsize())

        # Drain ACKs from root on the control lane (discard — they are flow credits)
        if cfg.ack_enabled:
            while True:
                ack = adapter.recv_buffered("root:control", timeout=0.0)
                if ack is None:
                    break
                if isinstance(ack.payload, AckMessage):
                    stats.acks_received += ack.payload.ack_count


_OBS_LANE_CYCLE = ("logs", "monitoring", "traces")


async def _stage_runner_loop(
    adapter:   PipeExecutionIpcTransportAdapter,
    stage_idx: int,
    runner_q:  asyncio.Queue,
    cfg:       LaneStarConfig,
    stats:     StageStats,
    pub:       PipelineSimPublisher,
    stop:      asyncio.Event,
) -> None:
    _SAMPLE_EVERY = 20
    _obs_cycle_idx = 0

    while not stop.is_set() or not runner_q.empty():
        try:
            record: PipelineRecord = runner_q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.001)
            continue

        if cfg.node_latency_ms > 0:
            await asyncio.sleep(cfg.node_latency_ms / 1000.0)
        if cfg.sync_block_ms > 0:
            time.sleep(cfg.sync_block_ms / 1000.0)

        record.stage_exits_ns.append(time.monotonic_ns())
        record.target_stage = stage_idx + 1

        try:
            adapter.send("root:data", record, no_reply=True)
        except Exception:
            pass
        stats.processed += 1

        # OBS events — mode A: round-robin across lanes every N records
        if cfg.obs_every_n > 0 and stats.processed % cfg.obs_every_n == 0:
            lane = _OBS_LANE_CYCLE[_obs_cycle_idx % 3]
            _obs_cycle_idx += 1
            obs = ObsEvent(
                stage_name=f"stage:{stage_idx}",
                lane=lane,
                event="processed",
                ts_ns=time.monotonic_ns(),
                count=stats.processed,
            )
            try:
                adapter.send(f"root:{lane}", obs, no_reply=True)
                if lane == "logs":
                    stats.obs_logs_sent += 1
                elif lane == "monitoring":
                    stats.obs_mon_sent += 1
                else:
                    stats.obs_traces_sent += 1
            except Exception:
                pass

        # OBS events — mode B: N events on EACH of the 3 lanes per data message
        # Models: obs_steps_per_msg processing steps each generating log+metric+trace
        if cfg.obs_steps_per_msg > 0:
            now_ns = time.monotonic_ns()
            for step in range(cfg.obs_steps_per_msg):
                for lane, attr in (
                    ("logs",       "obs_logs_sent"),
                    ("monitoring", "obs_mon_sent"),
                    ("traces",     "obs_traces_sent"),
                ):
                    obs = ObsEvent(
                        stage_name=f"stage:{stage_idx}",
                        lane=lane,
                        event=f"step:{step}",
                        ts_ns=now_ns,
                        count=stats.processed,
                    )
                    try:
                        adapter.send(f"root:{lane}", obs, no_reply=True)
                        setattr(stats, attr, getattr(stats, attr) + 1)
                    except Exception:
                        pass

        if stats.processed % _SAMPLE_EVERY == 0:
            pub.publish(f"stage.{stage_idx}.tick", {
                "stage":     stage_idx,
                "processed": stats.processed,
                "runner_q":  runner_q.qsize(),
            })


# ---------------------------------------------------------------------------
# Sink process
# ---------------------------------------------------------------------------

def sink_main(
    pipe_set: LanedPipeSet,
    cfg:      LaneStarConfig,
    result_q: "mp.Queue",
    run_id:   str,
) -> None:
    pipe_set.close_parent_ends()

    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    pipe_set.attach_child_to_adapter(adapter, prefix="root")

    stats = SinkStats(pid=os.getpid())
    for _ in range(cfg.n_stages):
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
    cfg:     LaneStarConfig,
    stats:   SinkStats,
) -> None:
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        while True:
            msg = adapter.recv_buffered("root:data", timeout=0.0)
            if msg is None:
                break
            if not isinstance(msg.payload, PipelineRecord):
                continue
            record = msg.payload
            now_ns = time.monotonic_ns()

            stats.e2e_latencies_ms.append((now_ns - record.created_ns) / 1_000_000.0)
            stats.completed += 1

            if len(record.stage_exits_ns) == cfg.n_stages:
                prev_ns = record.created_ns
                for i, exit_ns in enumerate(record.stage_exits_ns):
                    if i < len(stats.per_hop_ms):
                        stats.per_hop_ms[i].append((exit_ns - prev_ns) / 1_000_000.0)
                    prev_ns = exit_ns


# ---------------------------------------------------------------------------
# OBS process — receives observability events on all three OBS lanes
# ---------------------------------------------------------------------------

def obs_main(
    pipe_set: LanedPipeSet,
    cfg:      LaneStarConfig,
    result_q: "mp.Queue",
    run_id:   str,
) -> None:
    pipe_set.close_parent_ends()

    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    pipe_set.attach_child_to_adapter(adapter, prefix="root")

    stats = ObsStats(pid=os.getpid())
    loop  = asyncio.new_event_loop()
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
    cfg:     LaneStarConfig,
    stats:   ObsStats,
) -> None:
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        for lane, counter_attr in (
            ("root:logs",       "recv_logs"),
            ("root:monitoring", "recv_mon"),
            ("root:traces",     "recv_traces"),
        ):
            while True:
                msg = adapter.recv_buffered(lane, timeout=0.0)
                if msg is None:
                    break
                if isinstance(msg.payload, ObsEvent):
                    setattr(stats, counter_attr, getattr(stats, counter_attr) + 1)
                    # Send ACK back to root on control lane (1 per OBS event received)
                    if cfg.ack_enabled:
                        try:
                            adapter.send("root:control", AckMessage(ack_count=1), no_reply=True)
                            stats.acks_sent += 1
                        except Exception:
                            pass


# ---------------------------------------------------------------------------
# Root routing loop (main process asyncio)
# ---------------------------------------------------------------------------

_ROOT_REDIS_SAMPLE_EVERY = 20


async def _root_routing_loop(
    adapter:  PipeExecutionIpcTransportAdapter,
    cfg:      LaneStarConfig,
    stats:    RootStats,
    stop:     asyncio.Event,
    pub:      PipelineSimPublisher,
) -> None:
    last_tick  = asyncio.get_event_loop().time()
    tick_count = 0

    while not stop.is_set():
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        now = asyncio.get_event_loop().time()
        stats.tick_actual_ms.append((now - last_tick) * 1000.0)
        last_tick  = now
        tick_count += 1

        _ack = AckMessage(ack_count=1)

        # 1. Drain source:data → stage:0:data
        for _ in range(cfg.drain_budget):
            msg = adapter.recv_buffered("source:data", timeout=0.0)
            if msg is None:
                break
            if isinstance(msg.payload, PipelineRecord):
                try:
                    adapter.send("stage:0:data", msg.payload, no_reply=True)
                    stats.routed_data += 1
                except Exception:
                    pass

        # 2. Drain each stage's data lane → next stage or sink; send ACK to stage:control
        for i in range(cfg.n_stages):
            for _ in range(cfg.drain_budget):
                msg = adapter.recv_buffered(f"stage:{i}:data", timeout=0.0)
                if msg is None:
                    break
                payload = msg.payload
                if isinstance(payload, PipelineRecord):
                    dest = (f"stage:{payload.target_stage}:data"
                            if payload.target_stage < cfg.n_stages
                            else "sink:data")
                    try:
                        adapter.send(dest, payload, no_reply=True)
                        stats.routed_data += 1
                    except Exception:
                        pass
                    if cfg.ack_enabled:
                        try:
                            adapter.send(f"stage:{i}:control", _ack, no_reply=True)
                            stats.acks_to_stages += 1
                        except Exception:
                            pass

        # 3. Drain each stage's OBS lanes → obs; send ACK to stage:control per OBS message
        for i in range(cfg.n_stages):
            for lane, counter_attr, dest_lane in (
                ("logs",       "routed_logs",   "logs"),
                ("monitoring", "routed_mon",    "monitoring"),
                ("traces",     "routed_traces", "traces"),
            ):
                for _ in range(cfg.drain_budget):
                    msg = adapter.recv_buffered(f"stage:{i}:{lane}", timeout=0.0)
                    if msg is None:
                        break
                    if isinstance(msg.payload, ObsEvent):
                        try:
                            adapter.send(f"obs:{dest_lane}", msg.payload, no_reply=True)
                            setattr(stats, counter_attr, getattr(stats, counter_attr) + 1)
                        except Exception:
                            pass
                        if cfg.ack_enabled:
                            try:
                                adapter.send(f"stage:{i}:control", _ack, no_reply=True)
                                stats.acks_to_stages += 1
                            except Exception:
                                pass

        # 4. Drain obs:control — consume ACKs sent by obs leaf (flow credits, discard)
        if cfg.ack_enabled:
            while True:
                ack = adapter.recv_buffered("obs:control", timeout=0.0)
                if ack is None:
                    break
                if isinstance(ack.payload, AckMessage):
                    stats.acks_from_obs += ack.payload.ack_count

        # 5. Sample to Redis
        if tick_count % _ROOT_REDIS_SAMPLE_EVERY == 0:
            pub.publish("root.routing.tick", {
                "tick_n":           tick_count,
                "tick_ms":          round(stats.tick_actual_ms[-1], 2),
                "routed_data":      stats.routed_data,
                "routed_obs":       stats.routed_logs + stats.routed_mon + stats.routed_traces,
                "acks_to_stages":   stats.acks_to_stages,
                "acks_from_obs":    stats.acks_from_obs,
                "n_endpoints":      cfg.n_endpoints(),
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
    return f"lane_star:{ts}:{slug}"


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_lane_star_scenario(label: str, cfg: LaneStarConfig) -> None:
    ctx      = mp.get_context("fork")
    result_q: mp.Queue = ctx.Queue()
    run_id   = _make_run_id(label)

    registered = register_pipeline_run(run_id, label)

    # --- Allocate LanedPipeSets for every process pair with root ---
    source_pipes = LanedPipeSet.create(ctx)

    stage_pipes: list[LanedPipeSet] = []
    for _ in range(cfg.n_stages):
        stage_pipes.append(LanedPipeSet.create(ctx))

    sink_pipes = LanedPipeSet.create(ctx)
    obs_pipes  = LanedPipeSet.create(ctx)

    # --- Fork all child processes ---
    procs: list[mp.Process] = []

    source_proc = ctx.Process(
        target=source_main,
        args=(source_pipes, cfg, result_q, run_id),
        name="lane-star-source", daemon=True,
    )
    source_proc.start()
    procs.append(source_proc)

    for i, sp in enumerate(stage_pipes):
        proc = ctx.Process(
            target=stage_main,
            args=(i, sp, cfg, result_q, run_id),
            name=f"lane-star-stage-{i}", daemon=True,
        )
        proc.start()
        procs.append(proc)

    sink_proc = ctx.Process(
        target=sink_main,
        args=(sink_pipes, cfg, result_q, run_id),
        name="lane-star-sink", daemon=True,
    )
    sink_proc.start()
    procs.append(sink_proc)

    obs_proc = ctx.Process(
        target=obs_main,
        args=(obs_pipes, cfg, result_q, run_id),
        name="lane-star-obs", daemon=True,
    )
    obs_proc.start()
    procs.append(obs_proc)

    # Close child-side connections in root process
    source_pipes.close_child_ends()
    for sp in stage_pipes:
        sp.close_child_ends()
    sink_pipes.close_child_ends()
    obs_pipes.close_child_ends()

    # --- Build root adapter with all (n_stages+3)×5 endpoints ---
    root_adapter = PipeExecutionIpcTransportAdapter(
        context=ctx,
        poll_interval_seconds=cfg.ipc_poll_interval_ms / 1000.0,
    )
    source_pipes.attach_parent_to_adapter(root_adapter, prefix="source")
    for i, sp in enumerate(stage_pipes):
        sp.attach_parent_to_adapter(root_adapter, prefix=f"stage:{i}")
    sink_pipes.attach_parent_to_adapter(root_adapter, prefix="sink")
    obs_pipes.attach_parent_to_adapter(root_adapter, prefix="obs")

    # --- Root Redis publisher ---
    root_pub = PipelineSimPublisher(
        run_id=run_id, process_id="root:lane_star:w0",
        process_role="root", worker_index=0,
    )
    root_pub.connect()

    time.sleep(0.15)  # let child reader threads start

    # --- Run root routing loop ---
    root_stats    = RootStats(pid=os.getpid())
    stop          = asyncio.Event()
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

    # --- Drain result_q before joining processes ---
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
    total_events = source_stats.sent + root_stats.routed_data
    root_pub.close(total_events=total_events)
    if registered:
        finalize_pipeline_run(run_id, total_events)

    # --- Write to Postgres ---
    _write_to_postgres(run_id, label, cfg, source_stats, stage_stats_list, sink_stats, obs_stats, root_stats)

    # --- Print report ---
    _print_report(label, cfg, source_stats, stage_stats_list, sink_stats, obs_stats, root_stats)


# ---------------------------------------------------------------------------
# Postgres writer
# ---------------------------------------------------------------------------

def _write_to_postgres(
    run_id:           str,
    label:            str,
    cfg:              LaneStarConfig,
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
            "stage_idx":       s.stage_idx,
            "pid":             s.pid,
            "received":        s.received,
            "processed":       s.processed,
            "tick_p50_ms":     _pct(sorted(s.tick_actual_ms), 0.50) if s.tick_actual_ms else None,
            "runner_q_max":    max(s.runner_q_depths) if s.runner_q_depths else None,
            "obs_logs_sent":   s.obs_logs_sent,
            "obs_mon_sent":    s.obs_mon_sent,
            "obs_traces_sent": s.obs_traces_sent,
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

    write_lane_run(
        run_id=run_id,
        label=label,
        topology="star_5lane",
        n_stages=cfg.n_stages,
        n_endpoints=cfg.n_endpoints(),
        source_rate_per_s=cfg.source_rate_per_s,
        node_latency_ms=cfg.node_latency_ms,
        sync_block_ms=cfg.sync_block_ms,
        tick_interval_ms=cfg.tick_interval_ms,
        ipc_poll_ms=cfg.ipc_poll_interval_ms,
        obs_every_n=cfg.obs_every_n,
        obs_steps_per_msg=cfg.obs_steps_per_msg,
        ack_enabled=cfg.ack_enabled,
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
        root_routed_logs=root_stats.routed_logs,
        root_routed_mon=root_stats.routed_mon,
        root_routed_traces=root_stats.routed_traces,
        root_acks_to_stages=root_stats.acks_to_stages,
        root_acks_from_obs=root_stats.acks_from_obs,
        obs_received=obs_stats.received,
        obs_acks_sent=obs_stats.acks_sent,
        stages=stage_rows,
        hops=hop_rows,
    )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(
    label:            str,
    cfg:              LaneStarConfig,
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

    n_endpoints = cfg.n_endpoints()

    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)
    print(
        f"  topology=star_5lane  n_stages={cfg.n_stages}  root_endpoints={n_endpoints}"
    )
    print(
        f"  rate={cfg.source_rate_per_s:.0f}/s  node={cfg.node_latency_ms:.1f}ms"
        f"  tick={cfg.tick_interval_ms:.0f}ms  ipc_poll={cfg.ipc_poll_interval_ms:.0f}ms"
        + (f"  obs_every={cfg.obs_every_n}" if cfg.obs_every_n else "")
        + (f"  obs_steps={cfg.obs_steps_per_msg}/msg×3lanes" if cfg.obs_steps_per_msg else "")
        + ("  ack_enabled" if cfg.ack_enabled else "")
    )
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
    total_obs = root_stats.routed_logs + root_stats.routed_mon + root_stats.routed_traces
    print(
        f"  Root routed:       {root_stats.routed_data} data"
        f" | {root_stats.routed_logs} logs"
        f" | {root_stats.routed_mon} mon"
        f" | {root_stats.routed_traces} traces"
        f"  (OBS total={total_obs})"
    )
    if cfg.ack_enabled:
        print(
            f"  ACKs root→stages:  {root_stats.acks_to_stages}"
            f"  (expected {source_stats.sent * (1 + cfg.obs_steps_per_msg * 3) * cfg.n_stages})"
        )
        print(
            f"  ACKs obs→root:     {root_stats.acks_from_obs}"
            f"  (expected {obs_stats.received})"
        )
        total_stage_acks = sum(s.acks_received for s in stage_stats_list)
        print(
            f"  ACKs recvd stages: {total_stage_acks}"
            f"  obs sent: {obs_stats.acks_sent}"
        )
    if obs_stats.received > 0:
        print(
            f"  OBS received:      {obs_stats.recv_logs} logs"
            f" | {obs_stats.recv_mon} mon"
            f" | {obs_stats.recv_traces} traces"
            f"  total={obs_stats.received}"
        )

    if e2e:
        print()
        print("  ── E2E latency (source stamp → sink receipt) ───────────────────")
        print(f"    P50: {_pct(e2e, 0.50):.1f}ms")
        print(f"    P90: {_pct(e2e, 0.90):.1f}ms")
        print(f"    P99: {_pct(e2e, 0.99):.1f}ms")
        print(f"    max: {max(e2e):.1f}ms")

    if sink_stats.per_hop_ms and any(sink_stats.per_hop_ms):
        print()
        print("  ── Per-hop latency ─────────────────────────────────────────────")
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
        print(f"  ── Root tick ({n_endpoints} endpoints, target={cfg.tick_interval_ms:.0f}ms) ─────────────────────")
        print(f"    P50={_pct(r_tick, 0.50):.2f}ms  P99={_pct(r_tick, 0.99):.2f}ms  fired={len(r_tick)}")

    print()
    print("  ── Per stage ───────────────────────────────────────────────────")
    for s in stage_stats_list:
        ticks = sorted(s.tick_actual_ms) if s.tick_actual_ms else []
        rq    = s.runner_q_depths
        obs_total = s.obs_logs_sent + s.obs_mon_sent + s.obs_traces_sent
        print(
            f"  stage:{s.stage_idx} (pid={s.pid}):"
            f"  recv={s.received}  proc={s.processed}"
            f"  tick_P50={_pct(ticks, 0.50):.2f}ms"
            f"  runner_q_max={max(rq) if rq else 0}"
            + (f"  obs={obs_total}(l={s.obs_logs_sent}/m={s.obs_mon_sent}/t={s.obs_traces_sent})"
               if obs_total else "")
        )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("Multi-lane star topology simulation  (Experiment A)")
    print("Source → Root → Stage-0 → ... → Stage-N → Sink  +  OBS leaf")
    print("5 OS pipes per process pair: data / control / logs / monitoring / traces")
    print()

    # A-1: Baseline — compare to PL-1 (single-lane star)
    run_lane_star_scenario(
        "A-1: Baseline  (star-5lane, 2 stages, 200/s, node=1ms, tick=5ms)",
        LaneStarConfig(
            n_stages=2, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0,
        ),
    )

    # A-3: Heavy OBS — all 3 OBS lanes active, compare to PL-4 (single-lane OBS)
    run_lane_star_scenario(
        "A-3: Heavy OBS  (star-5lane, 2 stages, 200/s, node=1ms, obs_every=5)",
        LaneStarConfig(
            n_stages=2, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, ipc_poll_interval_ms=5.0,
            obs_every_n=5, duration_s=5.0,
        ),
    )

    # A-4: Deep pipeline — compare to PL-2 (single-lane, 4 stages)
    run_lane_star_scenario(
        "A-4: Deep pipeline  (star-5lane, 4 stages, 200/s, node=1ms, tick=5ms)",
        LaneStarConfig(
            n_stages=4, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, ipc_poll_interval_ms=5.0, duration_s=5.0,
        ),
    )

    # A-5: Realistic load — 5 stage processes, 4 OBS steps per message per stage,
    # ACKs enabled on control lane.
    #
    # Traffic per data message:
    #   Each stage: 1 data result + (4 logs + 4 mon + 4 traces) = 13 msgs → root
    #   Root → each stage: 13 ACKs on stage:i:control
    #   Root → obs: 5 stages × 12 OBS = 60 OBS messages per data msg
    #   Obs → root: 60 ACKs on root:control per data msg
    #
    # At 200/s:
    #   ~12,000 OBS events/s delivered to obs leaf
    #   ~13,000 ACKs/s sent by root to stages (across 5 control lanes)
    #   ~12,000 ACKs/s sent by obs to root (on 1 control lane)
    run_lane_star_scenario(
        "A-5: Realistic load  (star-5lane, 5 stages, 200/s, obs_steps=4/msg, ACKs)",
        LaneStarConfig(
            n_stages=5, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, ipc_poll_interval_ms=5.0,
            obs_steps_per_msg=4, ack_enabled=True,
            duration_s=5.0, drain_extra_s=4.0,
        ),
    )


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
