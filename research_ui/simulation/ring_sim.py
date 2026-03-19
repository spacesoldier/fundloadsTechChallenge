"""
Ring topology simulation — Experiment B.

Topology
--------
Linear ring:
    Source → leaf:0 → leaf:1 → … → leaf:N-1 → Sink

    Each leaf sends OBS events DIRECTLY to the OBS leaf on 3 dedicated pipes
    (logs / monitoring / traces) — root is NOT in the OBS path.

    Root holds only control pipes (one per process, bidirectional).
    Root asyncio loop is near-idle: polls ctrl pipes for shutdown only.

Request-response ring (scenario B-4):
    Source → leaf:0 → … → leaf:N-1 → Sink
                                      ↓ (return pipe)
    Source ←────────── ReturnRecord ──┘

    Source measures round-trip time from send to return receipt.

OBS leaf — non-blocking fan-out
    Receives: n_stages × 3 OBS connections (direct from each leaf, no root relay)
    Fans out each event into 4 bounded asyncio queues:
        • Jaeger  — asyncio task, simulated UDP fire-and-forget (~0ms/batch)
        • Redis   — asyncio task, simulated pipeline SET  (~0.5ms/256-batch)
        • Kafka   — asyncio task, simulated batched produce (~2ms/64-batch)
        • Files   — asyncio task + ThreadPoolExecutor per leaf
                    (2 files/leaf: logs + traces; blocking write offloaded)
    If any queue is full, the event is dropped for that integration (counted).
    The OBS receiver loop NEVER blocks waiting for integrations.

Pipe layout per leaf (direct, no root in data path):
    data_in        recv from upstream (prev leaf or source)
    data_out       send to downstream (next leaf or sink)
    obs_logs_s     send logs to obs leaf
    obs_mon_s      send monitoring to obs leaf
    obs_traces_s   send traces to obs leaf
    ctrl           duplex with root (command plane, near-idle)

Scenarios:
    B-1  Linear, 2 stages, 200/s, no OBS  (baseline vs star A-1)
    B-2  Linear, 5 stages, 200/s, obs_steps=4/msg  (vs star A-5)
    B-3  Linear, 5 stages, 1000/s, obs_steps=4/msg  (stress: 5× A-5 rate)
    B-4  Request-response, 3 stages, 200/s, obs_steps=4/msg

Run:
    python research_ui/simulation/ring_sim.py
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_HERE         = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]
_SRC          = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from ring_sim_postgres import write_ring_run   # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class RingConfig:
    n_stages:          int   = 2
    source_rate_per_s: float = 200.0
    node_latency_ms:   float = 1.0     # async sleep per record per leaf
    tick_interval_ms:  float = 5.0     # scheduler tick for all processes
    drain_budget:      int   = 32      # max records drained per tick per connection
    obs_steps_per_msg: int   = 0       # OBS events per data msg per lane (0 = off)
    ring_return:       bool  = False   # request-response variant
    duration_s:        float = 5.0
    drain_extra_s:     float = 3.0
    # OBS integration simulated throughput limits
    # Each batch is processed in one asyncio.sleep() call (non-blocking)
    jaeger_batch:      int   = 1024    # events per yield cycle (UDP: essentially free)
    redis_batch:       int   = 256     # events per pipeline batch → 0.5ms
    redis_batch_ms:    float = 0.5
    kafka_batch:       int   = 64      # events per produce batch → 2ms
    kafka_batch_ms:    float = 2.0
    file_batch:        int   = 256     # lines per executor write call
    obs_queue_size:    int   = 100_000


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@dataclass
class PipelineRecord:
    record_id:      int
    created_ns:     int
    stage_exits_ns: list[int] = field(default_factory=list)


@dataclass
class ReturnRecord:
    """Sent back from sink to source in ring_return variant."""
    record_id:      int
    created_ns:     int
    completed_ns:   int
    stage_exits_ns: list[int] = field(default_factory=list)


@dataclass
class ObsEvent:
    leaf_idx: int
    lane:     str   # "logs" | "monitoring" | "traces"
    event:    str
    ts_ns:    int


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class SourceStats:
    pid:         int         = 0
    sent:        int         = 0
    round_trips: list[float] = field(default_factory=list)  # ms, ring_return only


@dataclass
class LeafStats:
    leaf_idx:        int         = 0
    pid:             int         = 0
    received:        int         = 0
    processed:       int         = 0
    tick_actual_ms:  list[float] = field(default_factory=list)
    runner_q_depths: list[int]   = field(default_factory=list)
    obs_logs_sent:   int         = 0
    obs_mon_sent:    int         = 0
    obs_traces_sent: int         = 0


@dataclass
class SinkStats:
    pid:        int               = 0
    completed:  int               = 0
    e2e_ms:     list[float]       = field(default_factory=list)
    per_hop_ms: list[list[float]] = field(default_factory=list)


@dataclass
class IntegrationStats:
    jaeger_sent:    int = 0
    jaeger_dropped: int = 0
    redis_sent:     int = 0
    redis_dropped:  int = 0
    kafka_sent:     int = 0
    kafka_dropped:  int = 0
    file_written:   int = 0
    file_dropped:   int = 0


@dataclass
class ObsStats:
    pid:         int               = 0
    recv_logs:   int               = 0
    recv_mon:    int               = 0
    recv_traces: int               = 0
    ig:          IntegrationStats  = field(default_factory=IntegrationStats)

    @property
    def received(self) -> int:
        return self.recv_logs + self.recv_mon + self.recv_traces


@dataclass
class RootStats:
    pid:        int = 0
    ctrl_ticks: int = 0


# ---------------------------------------------------------------------------
# Pipe allocation
# ---------------------------------------------------------------------------

@dataclass
class SourcePipes:
    data_out: object   # mp.connection.Connection, send to leaf:0
    ctrl:     object   # duplex with root
    return_r: object   # recv ReturnRecord from sink (ring_return only, else None)


@dataclass
class LeafPipes:
    leaf_idx:    int
    data_in:     object   # recv from upstream
    data_out:    object   # send to downstream
    obs_logs_s:  object   # send logs to obs
    obs_mon_s:   object   # send monitoring to obs
    obs_traces_s: object  # send traces to obs
    ctrl:        object   # duplex with root


@dataclass
class SinkPipes:
    data_in:  object   # recv from last leaf
    ctrl:     object   # duplex with root
    return_s: object   # send ReturnRecord to source (ring_return only, else None)


@dataclass
class ObsPipes:
    logs_r:   list   # recv logs from each leaf [leaf_0_r, leaf_1_r, ...]
    mon_r:    list   # recv monitoring from each leaf
    traces_r: list   # recv traces from each leaf
    ctrl:     object


@dataclass
class RootPipes:
    source_ctrl: object
    leaf_ctrls:  list
    sink_ctrl:   object
    obs_ctrl:    object


def alloc_ring_pipes(n_stages: int, ring_return: bool = False):
    """Allocate all OS pipe connections for ring topology.

    Data chain: n_stages + 1 unidirectional pipes
        data[0]: source  → leaf:0
        data[i]: leaf:i-1 → leaf:i   (for i = 1..n_stages-1)
        data[n]: leaf:n-1 → sink

    OBS pipes: n_stages × 3 unidirectional (leaf → obs, per lane)
    Control:   n_stages + 3 duplex pipes (root ↔ each process)
    Return:    1 unidirectional pipe (sink → source, ring_return only)
    """
    # data_chain[i] = (recv_conn, send_conn)
    data_chain = [mp.Pipe(duplex=False) for _ in range(n_stages + 1)]

    return_r, return_w = mp.Pipe(duplex=False) if ring_return else (None, None)

    obs_logs   = [mp.Pipe(duplex=False) for _ in range(n_stages)]
    obs_mon    = [mp.Pipe(duplex=False) for _ in range(n_stages)]
    obs_traces = [mp.Pipe(duplex=False) for _ in range(n_stages)]

    # ctrl[i] = (root_end, process_end) — both duplex
    ctrl = [mp.Pipe(duplex=True) for _ in range(n_stages + 3)]
    # ctrl[0]         → source
    # ctrl[1..n]      → leaf:0..n-1
    # ctrl[n+1]       → sink
    # ctrl[n+2]       → obs

    source = SourcePipes(
        data_out=data_chain[0][1],
        ctrl=ctrl[0][1],
        return_r=return_r,
    )

    leaves = [
        LeafPipes(
            leaf_idx=i,
            data_in=data_chain[i][0],
            data_out=data_chain[i + 1][1],
            obs_logs_s=obs_logs[i][1],
            obs_mon_s=obs_mon[i][1],
            obs_traces_s=obs_traces[i][1],
            ctrl=ctrl[i + 1][1],
        )
        for i in range(n_stages)
    ]

    sink = SinkPipes(
        data_in=data_chain[n_stages][0],
        ctrl=ctrl[n_stages + 1][1],
        return_s=return_w,
    )

    obs = ObsPipes(
        logs_r  =[obs_logs[i][0]   for i in range(n_stages)],
        mon_r   =[obs_mon[i][0]    for i in range(n_stages)],
        traces_r=[obs_traces[i][0] for i in range(n_stages)],
        ctrl=ctrl[n_stages + 2][1],
    )

    root = RootPipes(
        source_ctrl=ctrl[0][0],
        leaf_ctrls =[ctrl[i + 1][0] for i in range(n_stages)],
        sink_ctrl  =ctrl[n_stages + 1][0],
        obs_ctrl   =ctrl[n_stages + 2][0],
    )

    # Collect all pipe ends the PARENT must close after all processes start
    # (parent doesn't participate in data/OBS/ctrl, only collects results)
    parent_close = (
        [data_chain[i][0] for i in range(n_stages + 1)]   # recv ends
        + [data_chain[i][1] for i in range(n_stages + 1)] # send ends
        + [obs_logs[i][0]   for i in range(n_stages)]
        + [obs_logs[i][1]   for i in range(n_stages)]
        + [obs_mon[i][0]    for i in range(n_stages)]
        + [obs_mon[i][1]    for i in range(n_stages)]
        + [obs_traces[i][0] for i in range(n_stages)]
        + [obs_traces[i][1] for i in range(n_stages)]
        + [ctrl[i][1] for i in range(n_stages + 3)]   # process ends of ctrl
        + ([return_r, return_w] if ring_return else [])
    )
    # Root itself keeps ctrl[i][0] ends — exclude those from close list
    parent_close_set = set(id(c) for c in parent_close if c is not None)

    return source, leaves, sink, obs, root, parent_close_set


# ---------------------------------------------------------------------------
# Source process
# ---------------------------------------------------------------------------

def source_main(cfg: RingConfig, pipes: SourcePipes, result_q: mp.Queue) -> None:
    stats    = SourceStats(pid=os.getpid())
    interval = 1.0 / cfg.source_rate_per_s
    deadline = time.monotonic() + cfg.duration_s
    rid      = 0

    while time.monotonic() < deadline:
        t0  = time.monotonic()
        rec = PipelineRecord(record_id=rid, created_ns=time.monotonic_ns())
        try:
            pipes.data_out.send(rec)
            stats.sent += 1
            rid += 1
        except Exception:
            pass

        if cfg.ring_return and pipes.return_r is not None:
            while pipes.return_r.poll(0):
                try:
                    ret = pipes.return_r.recv()
                    if isinstance(ret, ReturnRecord):
                        stats.round_trips.append(
                            (ret.completed_ns - ret.created_ns) / 1e6
                        )
                except Exception:
                    break

        elapsed = time.monotonic() - t0
        if elapsed < interval:
            time.sleep(interval - elapsed)

    # Drain return pipe during extra window
    if cfg.ring_return and pipes.return_r is not None:
        drain_end = time.monotonic() + cfg.drain_extra_s
        while time.monotonic() < drain_end:
            if pipes.return_r.poll(0.05):
                try:
                    ret = pipes.return_r.recv()
                    if isinstance(ret, ReturnRecord):
                        stats.round_trips.append(
                            (ret.completed_ns - ret.created_ns) / 1e6
                        )
                except Exception:
                    pass

    result_q.put(stats)


# ---------------------------------------------------------------------------
# Leaf process
# ---------------------------------------------------------------------------

def leaf_main(cfg: RingConfig, pipes: LeafPipes, result_q: mp.Queue) -> None:
    asyncio.run(_leaf_async_main(cfg, pipes, result_q))


async def _leaf_async_main(
    cfg: RingConfig, pipes: LeafPipes, result_q: mp.Queue
) -> None:
    stats    = LeafStats(leaf_idx=pipes.leaf_idx, pid=os.getpid())
    runner_q: asyncio.Queue[PipelineRecord] = asyncio.Queue()
    stop     = asyncio.Event()
    asyncio.get_event_loop().call_later(cfg.duration_s + cfg.drain_extra_s, stop.set)

    await asyncio.gather(
        _leaf_scheduler(cfg, pipes, runner_q, stats, stop),
        _leaf_runner(cfg, pipes, runner_q, stats, stop),
    )
    result_q.put(stats)


async def _leaf_scheduler(
    cfg:      RingConfig,
    pipes:    LeafPipes,
    runner_q: asyncio.Queue,
    stats:    LeafStats,
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
            if not pipes.data_in.poll(0):
                break
            try:
                msg = pipes.data_in.recv()
                if isinstance(msg, PipelineRecord):
                    stats.received += 1
                    await runner_q.put(msg)
                    drained += 1
            except Exception:
                break
        stats.runner_q_depths.append(runner_q.qsize())


_OBS_LANE_ATTRS = [
    ("logs",       "obs_logs_s",   "obs_logs_sent"),
    ("monitoring", "obs_mon_s",    "obs_mon_sent"),
    ("traces",     "obs_traces_s", "obs_traces_sent"),
]


async def _leaf_runner(
    cfg:      RingConfig,
    pipes:    LeafPipes,
    runner_q: asyncio.Queue,
    stats:    LeafStats,
    stop:     asyncio.Event,
) -> None:
    while not stop.is_set() or not runner_q.empty():
        try:
            rec = await asyncio.wait_for(runner_q.get(), timeout=0.05)
        except asyncio.TimeoutError:
            continue

        if cfg.node_latency_ms > 0:
            await asyncio.sleep(cfg.node_latency_ms / 1000.0)

        rec.stage_exits_ns.append(time.monotonic_ns())
        stats.processed += 1

        try:
            pipes.data_out.send(rec)
        except Exception:
            pass

        if cfg.obs_steps_per_msg > 0:
            now_ns = time.monotonic_ns()
            for step in range(cfg.obs_steps_per_msg):
                for lane, pipe_attr, stat_attr in _OBS_LANE_ATTRS:
                    obs = ObsEvent(
                        leaf_idx=pipes.leaf_idx,
                        lane=lane,
                        event=f"step:{step}",
                        ts_ns=now_ns,
                    )
                    conn = getattr(pipes, pipe_attr)
                    try:
                        conn.send(obs)
                        setattr(stats, stat_attr, getattr(stats, stat_attr) + 1)
                    except Exception:
                        pass


# ---------------------------------------------------------------------------
# Sink process
# ---------------------------------------------------------------------------

def sink_main(cfg: RingConfig, pipes: SinkPipes, result_q: mp.Queue) -> None:
    stats = SinkStats(pid=os.getpid())
    for _ in range(cfg.n_stages):
        stats.per_hop_ms.append([])

    deadline = time.monotonic() + cfg.duration_s + cfg.drain_extra_s

    while time.monotonic() < deadline:
        if not pipes.data_in.poll(0.01):
            continue
        try:
            msg = pipes.data_in.recv()
        except Exception:
            continue
        if not isinstance(msg, PipelineRecord):
            continue

        now_ns = time.monotonic_ns()
        stats.e2e_ms.append((now_ns - msg.created_ns) / 1e6)
        stats.completed += 1

        prev_ns = msg.created_ns
        for i, exit_ns in enumerate(msg.stage_exits_ns):
            if i < len(stats.per_hop_ms):
                stats.per_hop_ms[i].append((exit_ns - prev_ns) / 1e6)
            prev_ns = exit_ns

        if cfg.ring_return and pipes.return_s is not None:
            try:
                pipes.return_s.send(ReturnRecord(
                    record_id=msg.record_id,
                    created_ns=msg.created_ns,
                    completed_ns=now_ns,
                    stage_exits_ns=msg.stage_exits_ns,
                ))
            except Exception:
                pass

    result_q.put(stats)


# ---------------------------------------------------------------------------
# OBS process — non-blocking fan-out to integration backends
# ---------------------------------------------------------------------------

def obs_main(cfg: RingConfig, pipes: ObsPipes, result_q: mp.Queue) -> None:
    asyncio.run(_obs_async_main(cfg, pipes, result_q))


async def _obs_async_main(
    cfg: RingConfig, pipes: ObsPipes, result_q: mp.Queue
) -> None:
    stats = ObsStats(pid=os.getpid())
    ig    = stats.ig
    stop  = asyncio.Event()
    asyncio.get_event_loop().call_later(
        cfg.duration_s + cfg.drain_extra_s + 1.0, stop.set
    )

    # Bounded integration queues — put_nowait() drops if full (counted)
    jaeger_q = asyncio.Queue(maxsize=cfg.obs_queue_size)
    redis_q  = asyncio.Queue(maxsize=cfg.obs_queue_size)
    kafka_q  = asyncio.Queue(maxsize=cfg.obs_queue_size)
    # Per-leaf file queues: {"logs": Queue, "traces": Queue}
    file_qs  = {
        i: {
            "logs":   asyncio.Queue(maxsize=cfg.obs_queue_size),
            "traces": asyncio.Queue(maxsize=cfg.obs_queue_size),
        }
        for i in range(cfg.n_stages)
    }

    # Thread pool for file writes (blocking I/O offloaded, never blocks asyncio loop)
    executor = ThreadPoolExecutor(
        max_workers=max(2, cfg.n_stages),
        thread_name_prefix="obs_file",
    )

    def _fanout(evt: ObsEvent) -> None:
        """Distribute one OBS event to all integration queues (non-blocking)."""
        for q, drop_attr in (
            (jaeger_q, "jaeger_dropped"),
            (redis_q,  "redis_dropped"),
            (kafka_q,  "kafka_dropped"),
        ):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                setattr(ig, drop_attr, getattr(ig, drop_attr) + 1)

        # Files: logs → log file, traces → trace file (monitoring goes to redis/kafka only)
        fq = file_qs.get(evt.leaf_idx, {})
        target_fq = fq.get(evt.lane) if evt.lane in ("logs", "traces") else None
        if target_fq is not None:
            try:
                target_fq.put_nowait(evt)
            except asyncio.QueueFull:
                ig.file_dropped += 1

    # ── Receiver loop ─────────────────────────────────────────────────────────

    async def _receiver() -> None:
        _all_logs   = pipes.logs_r
        _all_mon    = pipes.mon_r
        _all_traces = pipes.traces_r
        while not stop.is_set():
            await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
            for i in range(cfg.n_stages):
                # logs
                for _ in range(cfg.drain_budget):
                    if not _all_logs[i].poll(0):
                        break
                    try:
                        evt = _all_logs[i].recv()
                        if isinstance(evt, ObsEvent):
                            stats.recv_logs += 1
                            _fanout(evt)
                    except Exception:
                        break
                # monitoring
                for _ in range(cfg.drain_budget):
                    if not _all_mon[i].poll(0):
                        break
                    try:
                        evt = _all_mon[i].recv()
                        if isinstance(evt, ObsEvent):
                            stats.recv_mon += 1
                            _fanout(evt)
                    except Exception:
                        break
                # traces
                for _ in range(cfg.drain_budget):
                    if not _all_traces[i].poll(0):
                        break
                    try:
                        evt = _all_traces[i].recv()
                        if isinstance(evt, ObsEvent):
                            stats.recv_traces += 1
                            _fanout(evt)
                    except Exception:
                        break
        # Drain remaining after stop
        for i in range(cfg.n_stages):
            for conn, counter_attr in (
                (_all_logs[i],   "recv_logs"),
                (_all_mon[i],    "recv_mon"),
                (_all_traces[i], "recv_traces"),
            ):
                while conn.poll(0):
                    try:
                        evt = conn.recv()
                        if isinstance(evt, ObsEvent):
                            setattr(stats, counter_attr,
                                    getattr(stats, counter_attr) + 1)
                            _fanout(evt)
                    except Exception:
                        break

    # ── Integration writer tasks ───────────────────────────────────────────────

    async def _jaeger_writer() -> None:
        """Simulated UDP: drain entire queue, yield once. Near-zero latency."""
        while not stop.is_set() or not jaeger_q.empty():
            batch = []
            try:
                while len(batch) < cfg.jaeger_batch:
                    batch.append(jaeger_q.get_nowait())
            except asyncio.QueueFull:
                pass
            except Exception:
                pass
            if batch:
                await asyncio.sleep(0)   # yield to other tasks
                ig.jaeger_sent += len(batch)
            else:
                await asyncio.sleep(0.001)

    async def _redis_writer() -> None:
        """Simulated pipeline: drain batch, sleep to simulate RTT."""
        while not stop.is_set() or not redis_q.empty():
            batch = []
            try:
                while len(batch) < cfg.redis_batch:
                    batch.append(redis_q.get_nowait())
            except Exception:
                pass
            if batch:
                await asyncio.sleep(cfg.redis_batch_ms / 1000.0)
                ig.redis_sent += len(batch)
            else:
                await asyncio.sleep(0.001)

    async def _kafka_writer() -> None:
        """Simulated batched produce: accumulate up to batch_size, then sleep."""
        while not stop.is_set() or not kafka_q.empty():
            batch = []
            # Accumulate with short timeout to fill batch naturally
            try:
                item = await asyncio.wait_for(kafka_q.get(), timeout=0.005)
                batch.append(item)
            except asyncio.TimeoutError:
                pass
            if batch:
                try:
                    while len(batch) < cfg.kafka_batch:
                        batch.append(kafka_q.get_nowait())
                except Exception:
                    pass
                await asyncio.sleep(cfg.kafka_batch_ms / 1000.0)
                ig.kafka_sent += len(batch)

    def _sync_write_line(path: Path, line: str) -> None:
        """Blocking file append — runs inside ThreadPoolExecutor."""
        with path.open("a") as fh:
            fh.write(line)

    async def _file_writer(leaf_idx: int, lane: str) -> None:
        """
        Per-leaf per-lane file writer.
        Drains queue into batches; offloads actual write to thread pool.
        Models: 2 files per leaf (logs + traces) with non-blocking asyncio pattern.
        """
        loop    = asyncio.get_running_loop()
        fq      = file_qs[leaf_idx][lane]
        outpath = Path(f"/tmp/ring_sim_leaf{leaf_idx}_{lane}.log")
        while not stop.is_set() or not fq.empty():
            await asyncio.sleep(0.002)   # yield; batches accumulate naturally
            items = []
            try:
                while len(items) < cfg.file_batch:
                    items.append(fq.get_nowait())
            except Exception:
                pass
            if items:
                lines = "\n".join(
                    f"{ev.ts_ns} leaf:{ev.leaf_idx} {ev.lane} {ev.event}"
                    for ev in items
                ) + "\n"
                try:
                    await loop.run_in_executor(
                        executor, _sync_write_line, outpath, lines
                    )
                    ig.file_written += len(items)
                except Exception:
                    ig.file_dropped += len(items)

    # ── Launch all tasks and wait ──────────────────────────────────────────────

    tasks = [
        asyncio.create_task(_receiver()),
        asyncio.create_task(_jaeger_writer()),
        asyncio.create_task(_redis_writer()),
        asyncio.create_task(_kafka_writer()),
    ]
    for i in range(cfg.n_stages):
        tasks.append(asyncio.create_task(_file_writer(i, "logs")))
        tasks.append(asyncio.create_task(_file_writer(i, "traces")))

    await asyncio.gather(*tasks)
    executor.shutdown(wait=False)
    result_q.put(stats)


# ---------------------------------------------------------------------------
# Root process — command plane only (near-idle)
# ---------------------------------------------------------------------------

def root_main(cfg: RingConfig, pipes: RootPipes, result_q: mp.Queue) -> None:
    asyncio.run(_root_async_main(cfg, pipes, result_q))


async def _root_async_main(
    cfg: RingConfig, pipes: RootPipes, result_q: mp.Queue
) -> None:
    stats    = RootStats(pid=os.getpid())
    deadline = asyncio.get_event_loop().time() + cfg.duration_s + cfg.drain_extra_s + 0.5

    all_ctrl = [pipes.source_ctrl] + pipes.leaf_ctrls + [pipes.sink_ctrl, pipes.obs_ctrl]

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(cfg.tick_interval_ms / 1000.0)
        stats.ctrl_ticks += 1
        # Poll control pipes — drain any messages (ignored in simulation)
        for conn in all_ctrl:
            while conn.poll(0):
                try:
                    conn.recv()
                except Exception:
                    break

    result_q.put(stats)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(int(len(s) * p), len(s) - 1)
    return round(s[idx], 2)


def _hop_label(i: int, n_stages: int) -> str:
    if i == 0:
        return "src→leaf:0"
    src = f"leaf:{i-1}"
    dst = f"leaf:{i}" if i < n_stages else "sink"
    return f"{src}→{dst}"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_ring_scenario(label: str, cfg: RingConfig) -> None:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"_{label[:8].replace(' ', '_')}"
    scenario_id = label.split(":")[0].strip() if ":" in label else "B-?"

    print(f"\n{'=' * 72}")
    print(f"  {label}")
    print(f"{'=' * 72}")
    print(
        f"  topology=ring_5lane  n_stages={cfg.n_stages}"
        f"  ring_return={cfg.ring_return}"
    )
    print(
        f"  rate={cfg.source_rate_per_s:.0f}/s  node={cfg.node_latency_ms:.1f}ms"
        f"  tick={cfg.tick_interval_ms:.0f}ms"
        + (f"  obs_steps={cfg.obs_steps_per_msg}/msg×3lanes" if cfg.obs_steps_per_msg else "")
    )

    source_pipes, leaf_pipes, sink_pipes, obs_pipes, root_pipes, parent_close_ids = \
        alloc_ring_pipes(cfg.n_stages, cfg.ring_return)

    result_q: mp.Queue = mp.Queue()

    procs = []

    source_proc = mp.Process(
        target=source_main, args=(cfg, source_pipes, result_q), daemon=True
    )
    procs.append(source_proc)

    leaf_procs = [
        mp.Process(target=leaf_main, args=(cfg, lp, result_q), daemon=True)
        for lp in leaf_pipes
    ]
    procs.extend(leaf_procs)

    sink_proc = mp.Process(
        target=sink_main, args=(cfg, sink_pipes, result_q), daemon=True
    )
    procs.append(sink_proc)

    obs_proc = mp.Process(
        target=obs_main, args=(cfg, obs_pipes, result_q), daemon=True
    )
    procs.append(obs_proc)

    root_proc = mp.Process(
        target=root_main, args=(cfg, root_pipes, result_q), daemon=True
    )
    procs.append(root_proc)

    for p in procs:
        p.start()

    print(
        f"  PIDs: root={root_proc.pid}  source={source_proc.pid}"
        f"  sink={sink_proc.pid}  obs={obs_proc.pid}"
    )

    # Close all pipe ends the parent doesn't need
    # (root keeps root_pipes.*_ctrl ends; everything else can be closed)
    all_parent_closeable = [
        source_pipes.data_out, source_pipes.ctrl,
        *([] if not cfg.ring_return or source_pipes.return_r is None
          else [source_pipes.return_r]),
        *[lp.data_in for lp in leaf_pipes],
        *[lp.data_out for lp in leaf_pipes],
        *[lp.obs_logs_s for lp in leaf_pipes],
        *[lp.obs_mon_s for lp in leaf_pipes],
        *[lp.obs_traces_s for lp in leaf_pipes],
        *[lp.ctrl for lp in leaf_pipes],
        sink_pipes.data_in, sink_pipes.ctrl,
        *([] if not cfg.ring_return or sink_pipes.return_s is None
          else [sink_pipes.return_s]),
        *obs_pipes.logs_r, *obs_pipes.mon_r, *obs_pipes.traces_r,
        obs_pipes.ctrl,
    ]
    for conn in all_parent_closeable:
        try:
            conn.close()
        except Exception:
            pass

    # Collect results (n_stages leaves + source + sink + obs + root)
    n_expected = cfg.n_stages + 4
    results: dict = {}
    timeout = cfg.duration_s + cfg.drain_extra_s + 8.0
    deadline = time.monotonic() + timeout

    import queue as _queue
    while len(results) < n_expected and time.monotonic() < deadline:
        try:
            obj = result_q.get(timeout=1.0)
            key = type(obj).__name__
            if key == "LeafStats":
                results[f"leaf_{obj.leaf_idx}"] = obj
            elif key not in results:
                results[key] = obj
        except _queue.Empty:
            pass   # keep polling until deadline
        except Exception:
            break

    for p in procs:
        p.terminate()
        p.join(timeout=2.0)

    source_stats: SourceStats = results.get("SourceStats", SourceStats())
    sink_stats:   SinkStats   = results.get("SinkStats",   SinkStats())
    obs_stats:    ObsStats    = results.get("ObsStats",    ObsStats())
    root_stats:   RootStats   = results.get("RootStats",   RootStats())
    leaf_stats:   list[LeafStats] = [
        results.get(f"leaf_{i}", LeafStats(leaf_idx=i))
        for i in range(cfg.n_stages)
    ]

    _print_report(cfg, label, source_stats, sink_stats, obs_stats, root_stats, leaf_stats)
    _write_to_postgres(
        run_id, label, scenario_id, cfg,
        source_stats, sink_stats, obs_stats, root_stats, leaf_stats,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(
    cfg:          RingConfig,
    label:        str,
    source_stats: SourceStats,
    sink_stats:   SinkStats,
    obs_stats:    ObsStats,
    root_stats:   RootStats,
    leaf_stats:   list[LeafStats],
) -> None:
    dur = cfg.duration_s
    e2e = sorted(sink_stats.e2e_ms)
    ig  = obs_stats.ig

    print(f"\n  ── Throughput ───────────────────────────────────────────────────")
    print(f"  Source sent:       {source_stats.sent}")
    print(
        f"  Sink completed:    {sink_stats.completed}"
        f"  ({sink_stats.completed / dur:.1f}/s)"
    )
    eff = sink_stats.completed / max(1, source_stats.sent) * 100
    print(f"  Efficiency:        {eff:.1f}%")

    if obs_stats.received > 0:
        total_obs = source_stats.sent * cfg.n_stages * cfg.obs_steps_per_msg * 3
        print(
            f"  OBS received:      {obs_stats.recv_logs} logs"
            f" | {obs_stats.recv_mon} mon"
            f" | {obs_stats.recv_traces} traces"
            f"  total={obs_stats.received}"
            + (f"  (expected≈{total_obs})" if total_obs else "")
        )
        print(
            f"  Integrations:      "
            f"jaeger={ig.jaeger_sent}(drop={ig.jaeger_dropped})"
            f"  redis={ig.redis_sent}(drop={ig.redis_dropped})"
            f"  kafka={ig.kafka_sent}(drop={ig.kafka_dropped})"
            f"  files={ig.file_written}(drop={ig.file_dropped})"
        )

    print(f"\n  ── E2E latency (source stamp → sink receipt) ────────────────────")
    if e2e:
        print(f"    P50: {_pct(e2e, 0.50)}ms")
        print(f"    P90: {_pct(e2e, 0.90)}ms")
        print(f"    P99: {_pct(e2e, 0.99)}ms")
        print(f"    max: {max(e2e):.1f}ms")
    else:
        print("    (no data)")

    if cfg.ring_return and source_stats.round_trips:
        rtt = sorted(source_stats.round_trips)
        print(f"\n  ── Round-trip time (source send → return receipt) ───────────────")
        print(f"    P50: {_pct(rtt, 0.50)}ms")
        print(f"    P90: {_pct(rtt, 0.90)}ms")
        print(f"    P99: {_pct(rtt, 0.99)}ms")
        print(f"    max: {max(rtt):.1f}ms")
        print(f"    n:   {len(rtt)}")

    print(f"\n  ── Per-hop latency ──────────────────────────────────────────────")
    for i, hops in enumerate(sink_stats.per_hop_ms):
        if hops:
            hs = sorted(hops)
            lbl = _hop_label(i, cfg.n_stages)
            print(
                f"  hop {i}  {lbl:20s}  "
                f"P50={_pct(hs, 0.50)}ms  "
                f"P90={_pct(hs, 0.90)}ms  "
                f"P99={_pct(hs, 0.99)}ms"
            )

    print(f"\n  ── Root (command plane, {root_stats.ctrl_ticks} ctrl ticks) ──────────────────────")
    print(f"    root pid={root_stats.pid} — data routed: 0 (ring topology)")

    print(f"\n  ── Per leaf ─────────────────────────────────────────────────────")
    for ls in leaf_stats:
        t_sorted = sorted(ls.tick_actual_ms) if ls.tick_actual_ms else []
        q_max    = max(ls.runner_q_depths) if ls.runner_q_depths else 0
        obs_total = ls.obs_logs_sent + ls.obs_mon_sent + ls.obs_traces_sent
        print(
            f"  leaf:{ls.leaf_idx} (pid={ls.pid}):  "
            f"recv={ls.received}  proc={ls.processed}"
            f"  tick_P50={_pct(t_sorted, 0.50)}ms"
            f"  runner_q_max={q_max}"
            + (f"  obs={obs_total}(l={ls.obs_logs_sent}/m={ls.obs_mon_sent}/t={ls.obs_traces_sent})"
               if obs_total else "")
        )


# ---------------------------------------------------------------------------
# Postgres writer
# ---------------------------------------------------------------------------

def _write_to_postgres(
    run_id:       str,
    label:        str,
    scenario_id:  str,
    cfg:          RingConfig,
    source_stats: SourceStats,
    sink_stats:   SinkStats,
    obs_stats:    ObsStats,
    root_stats:   RootStats,
    leaf_stats:   list[LeafStats],
) -> None:
    dur = cfg.duration_s
    ig  = obs_stats.ig
    e2e = sorted(sink_stats.e2e_ms)
    rtt = sorted(source_stats.round_trips) if cfg.ring_return else []

    leaf_rows = []
    for ls in leaf_stats:
        t_sorted = sorted(ls.tick_actual_ms) if ls.tick_actual_ms else []
        leaf_rows.append({
            "leaf_idx":       ls.leaf_idx,
            "pid":            ls.pid,
            "received":       ls.received,
            "processed":      ls.processed,
            "tick_p50_ms":    _pct(t_sorted, 0.50),
            "tick_p99_ms":    _pct(t_sorted, 0.99),
            "runner_q_max":   max(ls.runner_q_depths) if ls.runner_q_depths else 0,
            "obs_logs_sent":  ls.obs_logs_sent,
            "obs_mon_sent":   ls.obs_mon_sent,
            "obs_traces_sent": ls.obs_traces_sent,
        })

    hop_rows = []
    for i, hops in enumerate(sink_stats.per_hop_ms):
        if hops:
            hs = sorted(hops)
            hop_rows.append({
                "hop_idx":   i,
                "hop_label": _hop_label(i, cfg.n_stages),
                "p50_ms":    _pct(hs, 0.50),
                "p90_ms":    _pct(hs, 0.90),
                "p99_ms":    _pct(hs, 0.99),
                "max_ms":    round(max(hs), 2),
                "count":     len(hs),
            })

    ok = write_ring_run(
        run_id=run_id,
        label=label,
        scenario_id=scenario_id,
        n_stages=cfg.n_stages,
        ring_return=cfg.ring_return,
        source_rate_per_s=cfg.source_rate_per_s,
        node_latency_ms=cfg.node_latency_ms,
        tick_interval_ms=cfg.tick_interval_ms,
        obs_steps_per_msg=cfg.obs_steps_per_msg,
        duration_s=cfg.duration_s,
        total_sent=source_stats.sent,
        total_completed=sink_stats.completed,
        throughput_per_s=round(sink_stats.completed / dur, 1),
        efficiency_pct=round(sink_stats.completed / max(1, source_stats.sent) * 100, 1),
        e2e_p50_ms=_pct(e2e, 0.50),
        e2e_p90_ms=_pct(e2e, 0.90),
        e2e_p99_ms=_pct(e2e, 0.99),
        e2e_max_ms=round(max(e2e), 2) if e2e else None,
        rtt_p50_ms=_pct(rtt, 0.50),
        rtt_p90_ms=_pct(rtt, 0.90),
        rtt_p99_ms=_pct(rtt, 0.99),
        obs_total_received=obs_stats.received,
        jaeger_sent=ig.jaeger_sent,
        jaeger_dropped=ig.jaeger_dropped,
        redis_sent=ig.redis_sent,
        redis_dropped=ig.redis_dropped,
        kafka_sent=ig.kafka_sent,
        kafka_dropped=ig.kafka_dropped,
        file_written=ig.file_written,
        file_dropped=ig.file_dropped,
        leaves=leaf_rows,
        hops=hop_rows,
    )
    if ok:
        print(f"\n  [postgres] run saved: {run_id}")
    else:
        print(f"\n  [postgres] write skipped (check connection)")


# ---------------------------------------------------------------------------
# Scenarios and entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mp.set_start_method("spawn", force=True)

    # B-1: Baseline — 2 stages, no OBS, compare to star A-1 (37.6ms E2E)
    run_ring_scenario(
        "B-1: Linear ring   (2 stages, 200/s, no OBS)",
        RingConfig(
            n_stages=2, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, duration_s=5.0,
        ),
    )

    # B-2: Realistic OBS — 5 stages, obs_steps=4, compare to star A-5 (1195ms E2E)
    run_ring_scenario(
        "B-2: Linear ring   (5 stages, 200/s, obs_steps=4/msg)",
        RingConfig(
            n_stages=5, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, obs_steps_per_msg=4,
            duration_s=5.0, drain_extra_s=4.0,
        ),
    )

    # B-3: Stress test — 1000/s source rate (would OOM in star topology)
    run_ring_scenario(
        "B-3: Stress test   (5 stages, 1000/s, obs_steps=4/msg)",
        RingConfig(
            n_stages=5, source_rate_per_s=1000.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, obs_steps_per_msg=4,
            duration_s=5.0, drain_extra_s=6.0,
        ),
    )

    # B-4: Request-response ring — 3 stages, round-trip time measurement
    run_ring_scenario(
        "B-4: Round-trip    (3 stages, 200/s, obs_steps=4/msg, ring_return)",
        RingConfig(
            n_stages=3, source_rate_per_s=200.0, node_latency_ms=1.0,
            tick_interval_ms=5.0, obs_steps_per_msg=4, ring_return=True,
            duration_s=5.0, drain_extra_s=4.0,
        ),
    )


if __name__ == "__main__":
    main()
