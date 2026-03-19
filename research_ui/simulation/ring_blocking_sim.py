"""
Experiment D-4 — Realistic Multiprocess Ring with Blocking I/O.

Architecture
------------
Real OS processes connected by real IPC pipes (multiprocessing.Pipe).
Each leaf process runs a real asyncio event loop.

The key difference from ring_sim.py: each leaf dynamically loads its
processing function from a `stages.*` module at startup, via importlib.
This is the same pattern that a Celery worker uses to discover tasks.

    leaf_main(pipes, spec_data, result_q)
        spec  = StageSpec(**spec_data)
        fn    = spec.load_fn()              ← importlib.import_module
        res   = spec.build_resources()      ← asyncio.Semaphore, Queues, CB
        asyncio.run(_leaf_async_main(..., fn, res))

Stage functions live in research_ui/simulation/stages/:
    cpu_stage.py  — constant delay
    db_stage.py   — asyncio.Semaphore pool + lognormal latency
    api_stage.py  — asyncio.Semaphore + lognormal + CircuitBreaker
    gpu_stage.py  — batch queue + background batcher asyncio.Task

Migration path
--------------
  Simulation → Integration: replace stages/* with real asyncpg / httpx / torch
  Integration → Celery:     wrap StageSpec as @app.task; replace pipe send
                             with next_task.delay(); move build_resources() to
                             Celery worker_init signal handler

Scenarios (Experiment D-4 — validation against SimPy C-1, C-3, C-4)
----------------------------------------------------------------------
  D4-C1  DB pool validation:   pool=2 vs pool=10, rates 50/200/500/s
  D4-C3  GPU batch validation:  strategy=fixed/timeout/adaptive, rates 10/15/s
  D4-C4  Combined + CB:         full 5-stage pipeline, rate sweep + circuit breaker

Run:
    cd research_ui
    poetry run python simulation/ring_blocking_sim.py
"""
from __future__ import annotations

import asyncio
import importlib
import math
import multiprocessing as mp
import os
import queue as _queue
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

_HERE         = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]
_SIM_DIR      = _HERE.parent
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))

_rng = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# Record — carries payload + enrichable context
# ---------------------------------------------------------------------------

@dataclass
class PipelineRecord:
    record_id:      int
    created_ns:     int
    stage_exits_ns: list[int] = field(default_factory=list)
    context:        dict      = field(default_factory=dict)


# ---------------------------------------------------------------------------
# StageSpec — the "task definition" loaded by each leaf worker
# ---------------------------------------------------------------------------

@dataclass
class StageSpec:
    """
    Describes what a leaf process should do.

    code_path : dotted import path of the stage module inside the 'stages' package
                e.g. "stages.db_stage" → importlib loads stages/db_stage.py
    config    : dict passed to build_resources(); available to the stage function
                via the 'resources' dict.
    """
    code_path: str
    config:    dict = field(default_factory=dict)

    def load_fn(self):
        """Dynamically import the stage module and return its process() coroutine."""
        module = importlib.import_module(self.code_path)
        return module.process

    def build_resources(self, leaf_idx: int) -> dict:
        """
        Create all asyncio-native resources for this stage.
        Called inside the leaf's asyncio event loop (after asyncio.run() starts).
        """
        cfg = self.config
        kind = cfg.get("kind", "cpu")
        res: dict = {"leaf_idx": leaf_idx}

        if kind == "cpu":
            res["cpu_ms"] = cfg.get("cpu_ms", 1.0)

        elif kind == "db":
            res["db_semaphore"] = asyncio.Semaphore(cfg.get("pool_size", 5))
            res["db_mean_ms"]   = cfg.get("mean_ms", 10.0)
            res["db_sigma"]     = cfg.get("sigma", 0.5)
            res["db_stats"]     = {"queries": 0, "wait_ms": [], "query_ms": []}

        elif kind == "api":
            res["api_semaphore"]  = asyncio.Semaphore(cfg.get("concurrency", 5))
            res["api_mean_ms"]    = cfg.get("mean_ms", 100.0)
            res["api_sigma"]      = cfg.get("sigma", 0.8)
            res["api_timeout_ms"] = cfg.get("timeout_ms", 500.0)
            res["api_max_retries"]= cfg.get("max_retries", 1)
            res["api_stats"]      = {"calls": 0, "timeouts": 0, "retries": 0,
                                     "fast_fails": 0, "latency_ms": []}
            cb_cfg = cfg.get("circuit_breaker")
            if cb_cfg is not None:
                from stages.api_stage import CircuitBreaker
                res["circuit_breaker"] = CircuitBreaker(**cb_cfg)

        elif kind == "gpu":
            res["gpu_batch_queue"]    = asyncio.Queue()
            res["gpu_strategy"]       = cfg.get("strategy", "timeout")
            res["gpu_batch_size"]     = cfg.get("batch_size", 8)
            res["gpu_batch_timeout_ms"] = cfg.get("batch_timeout_ms", 50.0)
            res["gpu_mean_ms"]        = cfg.get("mean_ms", 50.0)
            res["gpu_efficiency"]     = cfg.get("efficiency", {
                1: 1.00, 2: 0.60, 4: 0.40, 8: 0.25, 16: 0.18, 32: 0.15,
            })
            res["gpu_stats"] = {"batches": 0, "batch_sizes": [], "wait_ms": []}

        return res


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class RingBlockingConfig:
    stage_specs:       list[StageSpec]
    source_rate_per_s: float = 100.0
    tick_interval_ms:  float = 5.0
    drain_budget:      int   = 64
    duration_s:        float = 30.0
    drain_extra_s:     float = 5.0
    label:             str   = ""


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class SourceStats:
    pid:  int = 0
    sent: int = 0


@dataclass
class LeafStats:
    leaf_idx:        int         = 0
    pid:             int         = 0
    received:        int         = 0
    processed:       int         = 0
    tick_actual_ms:  list[float] = field(default_factory=list)
    runner_q_depths: list[int]   = field(default_factory=list)
    # resource-specific stats forwarded from build_resources
    db_stats:        dict | None = None
    api_stats:       dict | None = None
    gpu_stats:       dict | None = None
    cb_trips:        int         = 0
    cb_fast_fails:   int         = 0


@dataclass
class SinkStats:
    pid:        int               = 0
    completed:  int               = 0
    e2e_ms:     list[float]       = field(default_factory=list)
    per_hop_ms: list[list[float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipe layout (no OBS in this experiment — keeps it comparable to SimPy C series)
# ---------------------------------------------------------------------------
# data_chain[i] = (recv, send)  — n_stages + 1 pipes, source→leaf:0→…→sink
# ctrl[i]       = (root_end, proc_end) — duplex, n_stages + 2 (src + leaves + sink)

@dataclass
class SourcePipes:
    data_out: object
    ctrl:     object


@dataclass
class LeafPipes:
    leaf_idx: int
    data_in:  object
    data_out: object
    ctrl:     object


@dataclass
class SinkPipes:
    data_in: object
    ctrl:    object


@dataclass
class RootPipes:
    source_ctrl: object
    leaf_ctrls:  list
    sink_ctrl:   object


def _alloc_pipes(n_stages: int):
    data_chain = [mp.Pipe(duplex=False) for _ in range(n_stages + 1)]
    ctrl       = [mp.Pipe(duplex=True)  for _ in range(n_stages + 2)]

    source = SourcePipes(data_out=data_chain[0][1], ctrl=ctrl[0][1])
    leaves = [
        LeafPipes(
            leaf_idx=i,
            data_in =data_chain[i][0],
            data_out=data_chain[i + 1][1],
            ctrl    =ctrl[i + 1][1],
        )
        for i in range(n_stages)
    ]
    sink   = SinkPipes(data_in=data_chain[n_stages][0], ctrl=ctrl[n_stages + 1][1])
    root   = RootPipes(
        source_ctrl=ctrl[0][0],
        leaf_ctrls =[ctrl[i + 1][0] for i in range(n_stages)],
        sink_ctrl  =ctrl[n_stages + 1][0],
    )

    # All pipe ends parent must close after processes start
    parent_close_ids = set(
        id(c)
        for c in (
            [data_chain[i][0] for i in range(n_stages + 1)]
            + [data_chain[i][1] for i in range(n_stages + 1)]
            + [ctrl[i][1] for i in range(n_stages + 2)]
        )
        if c is not None
    )
    return source, leaves, sink, root, parent_close_ids


# ---------------------------------------------------------------------------
# Source process
# ---------------------------------------------------------------------------

def source_main(
    cfg:      RingBlockingConfig,
    pipes:    SourcePipes,
    result_q: mp.Queue,
) -> None:
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
        elapsed = time.monotonic() - t0
        if elapsed < interval:
            time.sleep(interval - elapsed)

    result_q.put(stats)


# ---------------------------------------------------------------------------
# Leaf process
# ---------------------------------------------------------------------------

def leaf_main(
    cfg:           RingBlockingConfig,
    pipes:         LeafPipes,
    spec_data:     dict,            # serialisable form of StageSpec
    result_q:      mp.Queue,
) -> None:
    asyncio.run(_leaf_async_main(cfg, pipes, spec_data, result_q))


async def _leaf_async_main(
    cfg:       RingBlockingConfig,
    pipes:     LeafPipes,
    spec_data: dict,
    result_q:  mp.Queue,
) -> None:
    stats = LeafStats(leaf_idx=pipes.leaf_idx, pid=os.getpid())

    # Dynamic code loading — the "Celery worker discovers its task" step
    spec       = StageSpec(**spec_data)
    process_fn = spec.load_fn()
    resources  = spec.build_resources(pipes.leaf_idx)

    # Forward resource stat dicts to LeafStats for reporting
    stats.db_stats  = resources.get("db_stats")
    stats.api_stats = resources.get("api_stats")
    stats.gpu_stats = resources.get("gpu_stats")

    runner_q: asyncio.Queue[PipelineRecord] = asyncio.Queue()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_later(
        cfg.duration_s + cfg.drain_extra_s, stop.set
    )

    tasks = [
        asyncio.create_task(_leaf_scheduler(cfg, pipes, runner_q, stats, stop)),
        asyncio.create_task(_leaf_runner(cfg, pipes, process_fn, resources, runner_q, stats, stop)),
    ]

    # Start GPU batcher as a background task if this is a GPU stage
    if "gpu_batch_queue" in resources:
        from stages.gpu_stage import run_batcher
        batcher_task = asyncio.create_task(run_batcher(resources))
        tasks.append(batcher_task)

    await asyncio.gather(*tasks, return_exceptions=True)

    # Collect circuit breaker counters
    cb = resources.get("circuit_breaker")
    if cb is not None:
        stats.cb_trips      = cb.trips
        stats.cb_fast_fails = cb.fast_fails

    result_q.put(stats)


async def _leaf_scheduler(
    cfg:      RingBlockingConfig,
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


async def _leaf_runner(
    cfg:        RingBlockingConfig,
    pipes:      LeafPipes,
    process_fn,
    resources:  dict,
    runner_q:   asyncio.Queue,
    stats:      LeafStats,
    stop:       asyncio.Event,
) -> None:
    """
    Concurrent dispatcher: fires an asyncio.Task per message so that
    pool_size (Semaphore capacity) actually limits concurrency, not the runner.
    Matches the SimPy fire-and-forget dispatcher model.
    """
    pending: set[asyncio.Task] = set()

    async def _handle(rec: PipelineRecord) -> None:
        await process_fn(rec, resources)
        rec.stage_exits_ns.append(time.monotonic_ns())
        stats.processed += 1
        try:
            pipes.data_out.send(rec)
        except Exception:
            pass

    while not stop.is_set() or not runner_q.empty() or pending:
        # Reap finished tasks
        done = {t for t in pending if t.done()}
        pending -= done

        try:
            rec = await asyncio.wait_for(runner_q.get(), timeout=0.02)
            pending.add(asyncio.create_task(_handle(rec)))
        except asyncio.TimeoutError:
            if pending:
                await asyncio.sleep(0)   # yield to let in-flight tasks progress

    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Sink process
# ---------------------------------------------------------------------------

def sink_main(
    cfg:      RingBlockingConfig,
    pipes:    SinkPipes,
    n_stages: int,
    result_q: mp.Queue,
) -> None:
    stats    = SinkStats(pid=os.getpid())
    for _    in range(n_stages):
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

    result_q.put(stats)


# ---------------------------------------------------------------------------
# Root (monitoring only — no data routing)
# ---------------------------------------------------------------------------

def root_main(cfg: RingBlockingConfig, root: RootPipes, result_q: mp.Queue) -> None:
    deadline = time.monotonic() + cfg.duration_s + cfg.drain_extra_s + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.5)
    result_q.put({"root": "done"})


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(cfg: RingBlockingConfig) -> dict:
    n_stages  = len(cfg.stage_specs)
    result_q  = mp.Queue()

    source_pipes, leaf_pipes, sink_pipes, root_pipes, parent_close_ids = (
        _alloc_pipes(n_stages)
    )

    procs = []

    p = mp.Process(
        target=source_main,
        args=(cfg, source_pipes, result_q),
        daemon=True,
    )
    procs.append(p)

    for i, (lp, spec) in enumerate(zip(leaf_pipes, cfg.stage_specs)):
        p = mp.Process(
            target=leaf_main,
            args=(cfg, lp, {"code_path": spec.code_path, "config": spec.config}, result_q),
            daemon=True,
        )
        procs.append(p)

    procs.append(mp.Process(
        target=sink_main,
        args=(cfg, sink_pipes, n_stages, result_q),
        daemon=True,
    ))
    procs.append(mp.Process(
        target=root_main,
        args=(cfg, root_pipes, result_q),
        daemon=True,
    ))

    for p in procs:
        p.start()

    # Parent closes all pipe ends it doesn't use
    all_ends = (
        [source_pipes.data_out, source_pipes.ctrl]
        + [e for lp in leaf_pipes for e in (lp.data_in, lp.data_out, lp.ctrl)]
        + [sink_pipes.data_in, sink_pipes.ctrl]
        + [root_pipes.source_ctrl] + root_pipes.leaf_ctrls + [root_pipes.sink_ctrl]
    )
    for end in all_ends:
        if end is not None and id(end) in parent_close_ids:
            try:
                end.close()
            except Exception:
                pass

    # Collect results
    total_procs = len(procs)
    results     = []
    deadline    = time.monotonic() + cfg.duration_s + cfg.drain_extra_s + 15.0
    while len(results) < total_procs and time.monotonic() < deadline:
        try:
            r = result_q.get(timeout=1.0)
            results.append(r)
        except _queue.Empty:
            pass

    for p in procs:
        p.terminate()
        p.join(timeout=2.0)

    source_st = next((r for r in results if isinstance(r, SourceStats)), None)
    leaf_sts  = sorted(
        [r for r in results if isinstance(r, LeafStats)],
        key=lambda x: x.leaf_idx,
    )
    sink_st   = next((r for r in results if isinstance(r, SinkStats)), None)

    return {"source": source_st, "leaves": leaf_sts, "sink": sink_st, "cfg": cfg}


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

console = Console()


def _pct(data: list[float], p: float) -> str:
    if not data:
        return "-"
    return f"{float(np.percentile(data, p)):.1f}"


def _mean_f(data: list[float]) -> str:
    return f"{sum(data)/len(data):.1f}" if data else "-"


def _print_result(r: dict) -> None:
    cfg:    RingBlockingConfig = r["cfg"]
    src:    SourceStats | None = r["source"]
    leaves: list[LeafStats]   = r["leaves"]
    sink:   SinkStats | None  = r["sink"]

    eff_s   = cfg.duration_s
    sent    = src.sent if src else 0
    done    = sink.completed if sink else 0
    actual  = done / eff_s if eff_s > 0 else 0
    e2e     = sink.e2e_ms if sink else []

    console.rule(f"[bold cyan]{cfg.label}[/bold cyan]")
    console.print(
        f"  source {cfg.source_rate_per_s:.0f}/s  "
        f"sent={sent}  received={done}  actual={actual:.1f}/s"
    )
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
        "extra",
        show_lines=False,
    )
    for st in leaves:
        spec  = cfg.stage_specs[st.leaf_idx]
        kind  = spec.config.get("kind", "cpu")

        # hop latency from sink.per_hop_ms
        hop_ms: list[float] = []
        if sink and st.leaf_idx < len(sink.per_hop_ms):
            hop_ms = sink.per_hop_ms[st.leaf_idx]

        wait_ms: list[float] = []
        extra = ""
        if st.db_stats:
            wait_ms = st.db_stats["wait_ms"]
            extra   = f"queries={st.db_stats['queries']}"
        elif st.api_stats:
            wait_ms = st.api_stats.get("latency_ms", [])
            extra   = (
                f"timeouts={st.api_stats['timeouts']} "
                f"retries={st.api_stats['retries']} "
                f"CB_trips={st.cb_trips} "
                f"fast_fails={st.cb_fast_fails}"
            )
        elif st.gpu_stats:
            wait_ms = st.gpu_stats.get("wait_ms", [])
            sizes   = st.gpu_stats.get("batch_sizes", [])
            avg_sz  = f"{sum(sizes)/len(sizes):.1f}" if sizes else "-"
            extra   = f"batches={st.gpu_stats['batches']} avg_size={avg_sz}"

        tbl.add_row(
            str(st.leaf_idx),
            kind,
            str(st.processed),
            _pct(hop_ms, 50),
            _pct(hop_ms, 99),
            _pct(wait_ms, 50),
            _pct(wait_ms, 99),
            _mean_f([float(x) for x in st.runner_q_depths]),
            str(max(st.runner_q_depths)) if st.runner_q_depths else "-",
            extra,
        )
    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

def _spec(kind: str, **kw) -> StageSpec:
    return StageSpec(code_path=f"stages.{kind}_stage", config={"kind": kind, **kw})


def _cpu(ms: float = 1.0) -> StageSpec:
    return _spec("cpu", cpu_ms=ms)


# D4-C1: DB pool — validate SimPy C-1 on real processes
def scenarios_d4_c1() -> list[RingBlockingConfig]:
    cfgs = []
    for pool_size in (2, 10):
        for rate in (50, 200, 500):
            cfgs.append(RingBlockingConfig(
                label=f"D4-C1 pool={pool_size} rate={rate}/s",
                stage_specs=[
                    _cpu(1.0),
                    _cpu(1.0),
                    _spec("db", pool_size=pool_size, mean_ms=10.0, sigma=0.5),
                    _cpu(1.0),
                    _cpu(1.0),
                ],
                source_rate_per_s=float(rate),
                duration_s=15.0,
                drain_extra_s=3.0,
            ))
    return cfgs


# D4-C3: GPU batch strategies — validate SimPy C-3 on real processes
def scenarios_d4_c3() -> list[RingBlockingConfig]:
    cfgs = []
    gpu_eff = {1: 1.00, 2: 0.60, 4: 0.40, 8: 0.25, 16: 0.18, 32: 0.15}
    for strategy in ("timeout", "adaptive", "fixed"):
        for rate in (10, 15):
            cfgs.append(RingBlockingConfig(
                label=f"D4-C3 GPU={strategy} rate={rate}/s",
                stage_specs=[
                    _cpu(1.0),
                    _spec("gpu",
                          strategy=strategy,
                          batch_size=8,
                          batch_timeout_ms=50.0,
                          mean_ms=50.0,
                          efficiency=gpu_eff),
                    _cpu(1.0),
                    _cpu(1.0),
                    _cpu(1.0),
                ],
                source_rate_per_s=float(rate),
                duration_s=30.0,
                drain_extra_s=5.0,
            ))
    return cfgs


# D4-C4: Combined pipeline — DB + API (with/without circuit breaker) + GPU
def scenarios_d4_c4() -> list[RingBlockingConfig]:
    cfgs = []
    gpu_eff = {1: 1.00, 2: 0.60, 4: 0.40, 8: 0.25, 16: 0.18, 32: 0.15}
    for rate in (5, 10, 15, 20, 30):
        for cb_enabled in (False, True):
            cb_cfg = {"failure_threshold": 0.5, "window_size": 20, "cooldown_s": 3.0} \
                     if cb_enabled else None
            cfgs.append(RingBlockingConfig(
                label=f"D4-C4 rate={rate}/s CB={'on' if cb_enabled else 'off'}",
                stage_specs=[
                    _cpu(1.0),
                    _spec("db", pool_size=5, mean_ms=10.0, sigma=0.5),
                    _cpu(2.0),
                    _spec("api",
                          mean_ms=100.0, sigma=0.8,
                          timeout_ms=500.0, max_retries=1,
                          concurrency=10,
                          circuit_breaker=cb_cfg),
                    _spec("gpu",
                          strategy="timeout",
                          batch_size=8,
                          batch_timeout_ms=50.0,
                          mean_ms=50.0,
                          efficiency=gpu_eff),
                ],
                source_rate_per_s=float(rate),
                duration_s=30.0,
                drain_extra_s=5.0,
            ))
    return cfgs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_group(title: str, scenarios: list[RingBlockingConfig]) -> None:
    console.rule(f"[bold yellow]{title}[/bold yellow]")
    for cfg in scenarios:
        result = run_scenario(cfg)
        _print_result(result)


def main() -> None:
    mp.set_start_method("fork", force=True)
    console.print("\n[bold]Experiment D-4 — Realistic Multiprocess Ring with Blocking I/O[/bold]\n")
    _run_group("D4-C1: DB Pool (real processes vs SimPy C-1)", scenarios_d4_c1())
    _run_group("D4-C3: GPU Batch Strategies (real processes vs SimPy C-3)", scenarios_d4_c3())
    _run_group("D4-C4: Combined + Circuit Breaker", scenarios_d4_c4())


if __name__ == "__main__":
    main()
