"""
Experiment C — Blocking I/O in the Pipeline.

Models a ring-topology pipeline where stages may block on:
  - Postgres DB (connection pool, lognormal query latency)
  - External API  (lognormal latency, timeout, optional retry)
  - Shared GPU    (one device, two task types, configurable scheduling policy)

SimPy model: each stage runs a *dispatcher* coroutine that drains the input
queue and spawns a separate SimPy process per message.  This enables true
concurrency at each stage — pool_size limits how many messages can be in the
resource (DB/GPU) simultaneously, exactly like an async Python service with
a bounded connection pool.

Virtual time unit: seconds.  All latencies reported in milliseconds.

Scenarios
---------
  C-1  DB connection pool saturation — 5 stages, stage 2 calls DB
         pool sizes: 2, 5, 10, 20; rates: 10, 20, 50, 100, 200 msg/s
  C-2  External API tail latency propagation — stage 3 calls API
         rate: 10 msg/s (balanced), 18 msg/s (near-saturation)
  C-3  Shared GPU scheduling policy — stage 1 (image, 50ms) + stage 4 (text, 20ms)
         policies: fifo, priority, batched
  C-4  Combined model saturation sweep — DB + API + GPU all present
         rate sweep: 1..50 msg/s

Run:
    cd research_ui
    poetry run python simulation/blocking_io_sim.py
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Generator

import numpy as np
import simpy
from rich.console import Console
from rich.table import Table

_rng = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DBConfig:
    pool_size: int   = 5
    mean_ms:   float = 10.0
    sigma:     float = 0.5      # lognormal sigma


@dataclass
class APIConfig:
    mean_ms:     float = 100.0
    sigma:       float = 0.8    # lognormal sigma
    timeout_ms:  float = 500.0
    max_retries: int   = 1
    concurrency: int   = 10     # max simultaneous outbound API connections


@dataclass
class GPUConfig:
    capacity:   int  = 1
    policy:     str  = "fifo"   # "fifo" | "priority" | "batched"
    batch_size: int  = 4        # for "batched" only
    # task_type → (mean_ms, std_ms, simpy_priority)
    tasks: dict[str, tuple[float, float, int]] = field(default_factory=lambda: {
        "image_embedding":     (50.0, 8.0, 2),
        "text_classification": (20.0, 4.0, 1),  # lower number = higher priority
    })


@dataclass
class StageConfig:
    kind:     str   = "cpu"     # "cpu" | "db" | "api" | "gpu"
    cpu_ms:   float = 1.0       # constant processing time for kind="cpu"
    gpu_task: str   = ""        # task type name for kind="gpu"


@dataclass
class BlockingConfig:
    stages:             list[StageConfig]
    source_rate_per_s:  float = 10.0
    duration_s:         float = 120.0
    warmup_s:           float = 10.0
    queue_bound:        int   = 1000    # SimPy Store capacity per stage

    db:  DBConfig  = field(default_factory=DBConfig)
    api: APIConfig = field(default_factory=APIConfig)
    gpu: GPUConfig = field(default_factory=GPUConfig)

    label: str = ""


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass
class Message:
    msg_id:       int
    created_at:   float            # simpy time (seconds)
    hop_enter:    list[float] = field(default_factory=list)
    hop_exit:     list[float]  = field(default_factory=list)
    resource_wait:list[float]  = field(default_factory=list)   # per stage, seconds
    api_timeouts: int = 0
    api_retries:  int = 0
    timed_out:    bool = False


# ---------------------------------------------------------------------------
# Per-stage statistics
# ---------------------------------------------------------------------------

@dataclass
class StageStats:
    stage_idx:     int
    processed:     int         = 0
    proc_times:    list[float] = field(default_factory=list)   # ms (total stage time)
    wait_times:    list[float] = field(default_factory=list)   # ms (resource wait only)
    queue_samples: list[int]   = field(default_factory=list)   # sampled queue depth


@dataclass
class RunResult:
    label:          str
    config:         BlockingConfig
    stage_stats:    list[StageStats]
    e2e_latencies:  list[float]    # ms
    source_emitted: int
    sink_received:  int
    timeout_count:  int
    retry_count:    int
    duration_s:     float          # effective (warmup excluded)


# ---------------------------------------------------------------------------
# Latency samplers
# ---------------------------------------------------------------------------

def _lognormal_s(mean_ms: float, sigma: float) -> float:
    """Sample seconds from lognormal. mean_ms is approximate geometric mean."""
    mu = math.log(mean_ms / 1000.0)
    return float(np.exp(_rng.normal(mu, sigma)))


def _normal_s(mean_ms: float, std_ms: float) -> float:
    """Sample seconds from normal, clamped to 0."""
    return max(0.0, float(_rng.normal(mean_ms / 1000.0, std_ms / 1000.0)))


# ---------------------------------------------------------------------------
# Per-message processing generators (run as spawned SimPy processes)
# ---------------------------------------------------------------------------

def _process_message(
    env:       simpy.Environment,
    cfg:       BlockingConfig,
    s_cfg:     StageConfig,
    msg:       Message,
    out_q:     simpy.Store | None,
    stats:     StageStats,
    resources: dict[str, Any],
    warmup_s:  float,
    counters:  dict[str, int],
) -> Generator:
    """Process one message through one stage. Runs concurrently for each message."""
    t_start = env.now
    msg.hop_enter.append(t_start)
    wait_s = 0.0

    if s_cfg.kind == "cpu":
        yield env.timeout(s_cfg.cpu_ms / 1000.0)

    elif s_cfg.kind == "db":
        db_pool: simpy.Resource = resources["db"]
        with db_pool.request() as req:
            wait_start = env.now
            yield req
            wait_s = env.now - wait_start
            query_s = _lognormal_s(cfg.db.mean_ms, cfg.db.sigma)
            yield env.timeout(query_s)

    elif s_cfg.kind == "api":
        api_pool: simpy.Resource = resources["api"]
        timeout_s = cfg.api.timeout_ms / 1000.0
        with api_pool.request() as req:
            yield req
            attempt = 0
            while attempt <= cfg.api.max_retries:
                latency_s = _lognormal_s(cfg.api.mean_ms, cfg.api.sigma)
                if latency_s <= timeout_s:
                    yield env.timeout(latency_s)
                    break
                # timed out
                yield env.timeout(timeout_s)
                msg.api_timeouts += 1
                attempt += 1
                if attempt <= cfg.api.max_retries:
                    msg.api_retries += 1
                else:
                    msg.timed_out = True
        if env.now > warmup_s:
            counters["timeouts"] += msg.api_timeouts
            counters["retries"]  += msg.api_retries
            msg.api_timeouts = 0   # reset so we don't double-count
            msg.api_retries  = 0

    elif s_cfg.kind == "gpu":
        task_type = s_cfg.gpu_task
        task_cfg  = cfg.gpu.tasks[task_type]
        mean_ms, std_ms, priority = task_cfg
        policy = cfg.gpu.policy

        if policy == "fifo":
            gpu: simpy.Resource = resources["gpu_fifo"]
            with gpu.request() as req:
                wait_start = env.now
                yield req
                wait_s = env.now - wait_start
                yield env.timeout(_normal_s(mean_ms, std_ms))

        elif policy == "priority":
            gpu_p: simpy.PriorityResource = resources["gpu_priority"]
            with gpu_p.request(priority=priority) as req:
                wait_start = env.now
                yield req
                wait_s = env.now - wait_start
                yield env.timeout(_normal_s(mean_ms, std_ms))

        elif policy == "batched":
            scheduler: GPUBatchScheduler = resources["gpu_batched"]
            enqueue_t = env.now
            done_event = scheduler.submit(task_type, msg)
            yield done_event
            wait_s = msg._gpu_batch_wait   # set by scheduler

    t_end = env.now
    msg.hop_exit.append(t_end)
    msg.resource_wait.append(wait_s)

    if env.now > warmup_s:
        stats.processed += 1
        stats.proc_times.append((t_end - t_start) * 1000.0)
        if wait_s > 0.0:
            stats.wait_times.append(wait_s * 1000.0)

    if out_q is not None:
        yield out_q.put(msg)


# ---------------------------------------------------------------------------
# Stage dispatcher — drains queue, spawns a process per message
# ---------------------------------------------------------------------------

def _stage_dispatcher(
    env:       simpy.Environment,
    cfg:       BlockingConfig,
    s_cfg:     StageConfig,
    stage_idx: int,
    in_q:      simpy.Store,
    out_q:     simpy.Store | None,
    stats:     StageStats,
    resources: dict[str, Any],
    warmup_s:  float,
    counters:  dict[str, int],
) -> Generator:
    """
    Dispatcher coroutine: pulls messages from in_q and fires a child SimPy
    process per message.  This allows multiple messages to be in-flight at
    the same stage simultaneously, limited only by the resource pool capacity.
    """
    while True:
        msg: Message = yield in_q.get()
        if env.now > warmup_s:
            stats.queue_samples.append(len(in_q.items))
        env.process(_process_message(
            env, cfg, s_cfg, msg, out_q, stats,
            resources, warmup_s, counters,
        ))


# ---------------------------------------------------------------------------
# GPU batched scheduler
# ---------------------------------------------------------------------------

class GPUBatchScheduler:
    """
    Accumulates GPU tasks up to batch_size, then processes the batch at once.
    Submitted tasks receive a done_event they yield on.
    """

    def __init__(self, env: simpy.Environment, gpu_cfg: GPUConfig) -> None:
        self.env      = env
        self.gpu_cfg  = gpu_cfg
        self.queue: simpy.Store = simpy.Store(env)
        self._gpu     = simpy.Resource(env, capacity=gpu_cfg.capacity)
        env.process(self._scheduler_loop())

    def submit(self, task_type: str, msg: Message) -> simpy.Event:
        done_event = self.env.event()
        self.queue.put((task_type, msg, done_event, self.env.now))
        return done_event

    def _scheduler_loop(self) -> Generator:
        while True:
            # Wait for at least one task
            item = yield self.queue.get()
            batch: list[tuple] = [item]
            # Greedily drain up to batch_size - 1 more
            while len(batch) < self.gpu_cfg.batch_size and len(self.queue.items) > 0:
                batch.append(self.queue.items.pop(0))

            now = self.env.now
            for task_type, msg, _ev, enqueue_t in batch:
                msg._gpu_batch_wait = now - enqueue_t   # expose wait to caller

            with self._gpu.request() as req:
                yield req
                # Batch GPU time: peak task mean × N × amortisation factor
                peak_ms = max(self.gpu_cfg.tasks[t][0] for t, _, _, _ in batch)
                batch_ms = peak_ms * len(batch) * 0.4  # ~60% speedup from batching
                yield self.env.timeout(batch_ms / 1000.0)

            for _, _, done_event, _ in batch:
                done_event.succeed()


# ---------------------------------------------------------------------------
# Source and sink
# ---------------------------------------------------------------------------

def _source_process(
    env:        simpy.Environment,
    rate_per_s: float,
    stage0_q:   simpy.Store,
    emitted:    list[int],
    warmup_s:   float,
) -> Generator:
    interval = 1.0 / rate_per_s
    msg_id   = 0
    while True:
        yield env.timeout(interval)
        msg = Message(msg_id=msg_id, created_at=env.now)
        msg._gpu_batch_wait = 0.0   # sentinel for GPU batched path
        yield stage0_q.put(msg)     # blocks if queue full (natural backpressure)
        if env.now > warmup_s:
            emitted.append(msg_id)
        msg_id += 1


def _sink_process(
    env:      simpy.Environment,
    sink_q:   simpy.Store,
    results:  list[Message],
    warmup_s: float,
) -> Generator:
    while True:
        msg: Message = yield sink_q.get()
        if env.now > warmup_s:
            results.append(msg)


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------

def run_scenario(config: BlockingConfig) -> RunResult:
    env = simpy.Environment()

    n      = len(config.stages)
    queues = [simpy.Store(env, capacity=config.queue_bound) for _ in range(n + 1)]
    # sink queue is unbounded
    queues[n] = simpy.Store(env)

    # Resources
    resources: dict[str, Any] = {}
    if any(s.kind == "db" for s in config.stages):
        resources["db"] = simpy.Resource(env, capacity=config.db.pool_size)

    if any(s.kind == "api" for s in config.stages):
        resources["api"] = simpy.Resource(env, capacity=config.api.concurrency)

    if any(s.kind == "gpu" for s in config.stages):
        policy = config.gpu.policy
        if policy == "fifo":
            resources["gpu_fifo"] = simpy.Resource(env, capacity=config.gpu.capacity)
        elif policy == "priority":
            resources["gpu_priority"] = simpy.PriorityResource(env, capacity=config.gpu.capacity)
        elif policy == "batched":
            resources["gpu_batched"] = GPUBatchScheduler(env, config.gpu)

    counters: dict[str, int] = {"timeouts": 0, "retries": 0}
    stats = [StageStats(stage_idx=i) for i in range(n)]

    # Source
    emitted_ids: list[int] = []
    env.process(_source_process(
        env, config.source_rate_per_s, queues[0], emitted_ids, config.warmup_s,
    ))

    # Stage dispatchers
    for i, s_cfg in enumerate(config.stages):
        env.process(_stage_dispatcher(
            env, config, s_cfg, i,
            queues[i], queues[i + 1], stats[i],
            resources, config.warmup_s, counters,
        ))

    # Sink
    sink_msgs: list[Message] = []
    env.process(_sink_process(env, queues[n], sink_msgs, config.warmup_s))

    env.run(until=config.duration_s)

    e2e = [
        (msg.hop_exit[-1] - msg.created_at) * 1000.0
        for msg in sink_msgs
        if msg.hop_exit
    ]

    eff_duration = config.duration_s - config.warmup_s

    return RunResult(
        label          = config.label,
        config         = config,
        stage_stats    = stats,
        e2e_latencies  = e2e,
        source_emitted = len(emitted_ids),
        sink_received  = len(sink_msgs),
        timeout_count  = counters["timeouts"],
        retry_count    = counters["retries"],
        duration_s     = eff_duration,
    )


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: float) -> str:
    if not data:
        return "-"
    return f"{float(np.percentile(data, p)):.1f}"


def _mean_s(data: list[float]) -> str:
    return f"{statistics.mean(data):.1f}" if data else "-"


# ---------------------------------------------------------------------------
# Console reporter
# ---------------------------------------------------------------------------

console = Console()


def _print_result(result: RunResult) -> None:
    cfg = result.config
    eff = result.duration_s
    actual_rate = result.sink_received / eff if eff > 0 else 0

    console.rule(f"[bold cyan]{result.label}[/bold cyan]")
    console.print(
        f"  source: {cfg.source_rate_per_s}/s  "
        f"emitted: {result.source_emitted}  "
        f"received: {result.sink_received}  "
        f"actual: {actual_rate:.1f}/s  "
        f"timeouts: {result.timeout_count}  retries: {result.retry_count}"
    )

    e2e = result.e2e_latencies
    if e2e:
        console.print(
            f"  E2E  P50={_pct(e2e,50)}ms  "
            f"P90={_pct(e2e,90)}ms  "
            f"P99={_pct(e2e,99)}ms  "
            f"max={max(e2e):.0f}ms"
        )

    tbl = Table(
        "stage", "kind", "ok",
        "proc P50", "proc P99",
        "wait P50", "wait P99",
        "q mean", "q max",
        title="Stage details",
        show_lines=False,
    )
    for st in result.stage_stats:
        s_cfg = cfg.stages[st.stage_idx]
        kind_label = s_cfg.kind
        if s_cfg.gpu_task:
            kind_label += f"/{s_cfg.gpu_task[:4]}"
        tbl.add_row(
            str(st.stage_idx),
            kind_label,
            str(st.processed),
            _pct(st.proc_times, 50),
            _pct(st.proc_times, 99),
            _pct(st.wait_times, 50),
            _pct(st.wait_times, 99),
            _mean_s([float(x) for x in st.queue_samples]),
            str(max(st.queue_samples)) if st.queue_samples else "-",
        )
    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _cpu(ms: float = 1.0) -> StageConfig:
    return StageConfig(kind="cpu", cpu_ms=ms)

def _db() -> StageConfig:
    return StageConfig(kind="db")

def _api() -> StageConfig:
    return StageConfig(kind="api")

def _gpu(task: str) -> StageConfig:
    return StageConfig(kind="gpu", gpu_task=task)


# C-1: DB pool saturation
def scenarios_c1() -> list[BlockingConfig]:
    cfgs = []
    for pool_size in (2, 5, 10, 20):
        for rate in (10, 50, 200, 500):
            cfgs.append(BlockingConfig(
                label   = f"C-1 pool={pool_size} rate={rate}/s",
                stages  = [_cpu(), _cpu(), _db(), _cpu(), _cpu()],
                source_rate_per_s = float(rate),
                duration_s = 60.0,
                warmup_s   =  5.0,
                db = DBConfig(pool_size=pool_size, mean_ms=10.0, sigma=0.5),
            ))
    return cfgs


# C-2: External API tail latency propagation
def scenarios_c2() -> list[BlockingConfig]:
    cfgs = []
    for rate in (10.0, 18.0):
        cfgs.append(BlockingConfig(
            label   = f"C-2 API rate={rate}/s",
            stages  = [_cpu(), _cpu(), _cpu(), _api(), _cpu()],
            source_rate_per_s = rate,
            duration_s = 300.0,
            warmup_s   =  20.0,
            api = APIConfig(
                mean_ms=50.0, sigma=0.6,
                timeout_ms=500.0, max_retries=1, concurrency=5,
            ),
        ))
    return cfgs


# C-3: GPU scheduling policies
def scenarios_c3() -> list[BlockingConfig]:
    gpu_tasks = {
        "image_embedding":     (50.0, 8.0, 2),
        "text_classification": (20.0, 4.0, 1),
    }
    cfgs = []
    for policy in ("fifo", "priority", "batched"):
        for rate in (10.0, 15.0):
            cfgs.append(BlockingConfig(
                label   = f"C-3 GPU={policy} rate={rate}/s",
                stages  = [
                    _cpu(),
                    _gpu("image_embedding"),
                    _cpu(),
                    _cpu(),
                    _gpu("text_classification"),
                ],
                source_rate_per_s = rate,
                duration_s = 180.0,
                warmup_s   =  15.0,
                gpu = GPUConfig(
                    capacity=1, policy=policy, batch_size=4,
                    tasks=gpu_tasks,
                ),
            ))
    return cfgs


# C-4: Combined saturation sweep
def scenarios_c4() -> list[BlockingConfig]:
    gpu_tasks = {
        "image_embedding":     (50.0, 8.0, 2),
        "text_classification": (20.0, 4.0, 1),
    }
    cfgs = []
    for rate in (1, 2, 5, 8, 10, 12, 15, 20, 30, 50):
        cfgs.append(BlockingConfig(
            label   = f"C-4 combined rate={rate}/s",
            stages  = [
                _cpu(1.0),
                _db(),
                _cpu(2.0),
                _api(),
                _gpu("image_embedding"),
            ],
            source_rate_per_s = float(rate),
            duration_s = 120.0,
            warmup_s   =  15.0,
            db  = DBConfig(pool_size=5, mean_ms=10.0, sigma=0.5),
            api = APIConfig(mean_ms=100.0, sigma=0.8, timeout_ms=500.0, max_retries=1, concurrency=10),
            gpu = GPUConfig(capacity=1, policy="fifo", tasks=gpu_tasks),
        ))
    return cfgs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_group(title: str, scenarios: list[BlockingConfig]) -> None:
    console.rule(f"[bold yellow]{title}[/bold yellow]")
    for cfg in scenarios:
        result = run_scenario(cfg)
        _print_result(result)


def main() -> None:
    console.print("\n[bold]Experiment C — Blocking I/O in Pipeline (SimPy)[/bold]\n")
    _run_group("C-1: DB Connection Pool Saturation", scenarios_c1())
    _run_group("C-2: External API Tail Latency Propagation", scenarios_c2())
    _run_group("C-3: Shared GPU Scheduling Policies", scenarios_c3())
    _run_group("C-4: Combined Model — Saturation Sweep", scenarios_c4())


if __name__ == "__main__":
    main()
