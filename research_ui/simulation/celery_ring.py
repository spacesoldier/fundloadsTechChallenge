"""
Celery-based pipeline: direct equivalent of ring_blocking_sim.py.

Architecture
-----------
ring_blocking_sim  →  Celery equivalent
──────────────────────────────────────────
IPC pipe           →  Redis broker queue
leaf process       →  Celery worker (--concurrency N)
asyncio.Semaphore  →  Celery worker concurrency (-c N)
asyncio.Task pool  →  gevent/asyncio worker pool
StageSpec.load_fn  →  @app.task via importlib at worker startup
ring topology      →  Celery canvas chain() dispatched per message

Each stage is a Celery task that loads the stage processing function
via importlib (same stages/ package as ring_blocking_sim.py), runs it
with asyncio.run(), and passes the record to the next stage in the chain.

Running
-------
Start workers (one per stage, auto-scales concurrency via -c):
  celery -A celery_ring worker -Q cpu_stage  -c 20 -n cpu@%h   --loglevel=warning &
  celery -A celery_ring worker -Q db_stage   -c 10 -n db@%h    --loglevel=warning &
  celery -A celery_ring worker -Q api_stage  -c 10 -n api@%h   --loglevel=warning &
  celery -A celery_ring worker -Q cpu2_stage -c 20 -n cpu2@%h  --loglevel=warning &

Then run this script directly for the source + sink:
  python celery_ring.py

Or use run_experiment() from another script.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import pickle
import statistics
import time
from dataclasses import dataclass, field
from typing import Any  # noqa: F401 (used by dynamically-created tasks)

from celery import Celery, chain
from celery.result import AsyncResult

from simulation.record import Record

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

BROKER_URL  = os.environ.get("CELERY_BROKER",  "redis://localhost:6379/1")
BACKEND_URL = os.environ.get("CELERY_BACKEND", "redis://localhost:6379/2")

app = Celery("celery_ring", broker=BROKER_URL, backend=BACKEND_URL)

app.conf.update(
    task_serializer     = "pickle",
    result_serializer   = "pickle",
    accept_content      = ["pickle"],
    task_acks_late      = True,
    worker_prefetch_multiplier = 1,    # one task at a time per slot
    result_expires      = 300,         # 5-minute TTL on results
    task_track_started  = True,
)


# ---------------------------------------------------------------------------
# Stage task factory — one @app.task per stage queue
# ---------------------------------------------------------------------------

def _make_stage_task(queue_name: str, code_path: str, config: dict) -> Any:
    """
    Dynamically register a Celery task that:
      1. Loads the processing function from code_path via importlib.
      2. Runs it with asyncio.run() (each call is a fresh event loop).
      3. Returns the enriched record.
    The task is bound to `queue_name` so separate worker pools can subscribe
    to separate queues, giving independent concurrency control.
    """
    @app.task(name=queue_name, queue=queue_name, bind=True)
    def _stage_task(self, record_bytes: bytes, resources_bytes: bytes) -> bytes:
        record: Record = pickle.loads(record_bytes)
        resources: dict = pickle.loads(resources_bytes)

        module = importlib.import_module(code_path)
        process_fn = module.process

        # Asyncio resources (Semaphore, Queue) must be created inside the
        # event loop where they are used — we rebuild them per-task here.
        # For a real deployment, use worker initialiser to build shared state.
        loop = asyncio.new_event_loop()
        try:
            _init_resources(resources, loop)
            loop.run_until_complete(process_fn(record, resources))
        finally:
            loop.close()

        record.stage_exits_ns.append(time.monotonic_ns())
        return pickle.dumps(record)

    return _stage_task


def _init_resources(resources: dict, loop: asyncio.AbstractEventLoop) -> None:
    """Hydrate asyncio primitives that can't be pickled."""
    if "db_pool_size" in resources and "db_semaphore" not in resources:
        resources["db_semaphore"] = asyncio.Semaphore(resources["db_pool_size"])
    if "api_concurrency" in resources and "api_semaphore" not in resources:
        resources["api_semaphore"] = asyncio.Semaphore(resources["api_concurrency"])
    # GPU batch queue — per-task GPU batching doesn't work with this approach
    # because each Celery task call gets its own event loop and queue.
    # Real GPU batching requires a worker-level background batcher task
    # (see ring_blocking_sim.py _leaf_async_main()). Celery equivalent:
    # use a worker initialiser (@worker_init signal) to start the batcher.


# ---------------------------------------------------------------------------
# Pipeline spec
# ---------------------------------------------------------------------------

@dataclass
class StageSpec:
    name:      str    # unique, becomes the Celery queue + task name
    code_path: str    # dotted import path inside research_ui/simulation/
    config:    dict = field(default_factory=dict)

    def resources(self) -> dict:
        """Build the serialisable resources dict (no asyncio objects)."""
        cfg = dict(self.config)
        cfg["leaf_idx"] = self.name
        return cfg


@dataclass
class PipelineConfig:
    stages:      list[StageSpec]
    rate_per_s:  float = 10.0
    duration_s:  float = 30.0
    name:        str   = "celery_ring"


# ---------------------------------------------------------------------------
# Register stage tasks for the default pipeline
# ---------------------------------------------------------------------------
# These are registered at import time so workers that load this module
# pick up the task definitions automatically.

DEFAULT_PIPELINE = PipelineConfig(
    name="default",
    stages=[
        StageSpec("cpu_stage",  "simulation.stages.cpu_stage",
                  {"cpu_ms": 5.0}),
        StageSpec("db_stage",   "simulation.stages.db_stage",
                  {"db_pool_size": 10, "db_mean_ms": 10.0, "db_sigma": 0.5}),
        StageSpec("cpu2_stage", "simulation.stages.cpu_stage",
                  {"cpu_ms": 5.0}),
        StageSpec("api_stage",  "simulation.stages.api_stage",
                  {"api_concurrency": 10, "api_mean_ms": 100.0,
                   "api_sigma": 0.8, "api_timeout_ms": 500.0,
                   "api_max_retries": 1}),
    ],
)

_registered_tasks: dict[str, Any] = {}

def _register_pipeline(pipeline: PipelineConfig) -> None:
    for spec in pipeline.stages:
        if spec.name not in _registered_tasks:
            _registered_tasks[spec.name] = _make_stage_task(
                spec.name, spec.code_path, spec.config
            )

_register_pipeline(DEFAULT_PIPELINE)


# ---------------------------------------------------------------------------
# Source: dispatch records and collect results
# ---------------------------------------------------------------------------

def run_experiment(pipeline: PipelineConfig | None = None) -> dict:
    """
    Send records at the configured rate, collect AsyncResult handles,
    then wait for all to complete. Returns latency statistics.

    NOTE: Celery workers must be running before calling this. Start with:
      python -m celery -A simulation.celery_ring worker \\
        -Q cpu_stage,db_stage,cpu2_stage,api_stage \\
        -c 20 --loglevel=warning
    """
    if pipeline is None:
        pipeline = DEFAULT_PIPELINE

    _register_pipeline(pipeline)

    specs    = pipeline.stages
    interval = 1.0 / pipeline.rate_per_s
    end_time = time.monotonic() + pipeline.duration_s

    results: list[tuple[int, AsyncResult]] = []  # (created_ns, result)
    seq = 0

    print(f"\n{'─'*70}")
    print(f"  Celery pipeline '{pipeline.name}'")
    print(f"  stages: {[s.name for s in specs]}")
    print(f"  rate={pipeline.rate_per_s}/s  duration={pipeline.duration_s}s")
    print(f"  broker: {BROKER_URL}")
    print(f"{'─'*70}")

    dispatch_start = time.monotonic()

    while time.monotonic() < end_time:
        rec = Record(seq=seq)
        rec_bytes = pickle.dumps(rec)

        # Build Celery chain: stage0 | stage1 | ... | stageN
        # Celery passes the return value of each task as the first positional arg
        # of the next task. Our signature is (record_bytes, resources_bytes),
        # so stage0 gets (rec_bytes, resources0), stage1 gets
        # (result_of_stage0=rec_bytes, resources1), etc. — exactly right.
        first_task = _registered_tasks[specs[0].name].s(
            rec_bytes, pickle.dumps(specs[0].resources())
        )
        rest_tasks = [
            _registered_tasks[spec.name].s(pickle.dumps(spec.resources()))
            for spec in specs[1:]
        ]
        pipeline_chain = chain(first_task, *rest_tasks)

        async_result = pipeline_chain.apply_async()
        results.append((rec.created_ns, async_result))
        seq += 1

        # Rate limiting
        next_send = dispatch_start + seq * interval
        sleep_for = next_send - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    sent       = seq
    sent_rate  = sent / (time.monotonic() - dispatch_start)
    print(f"  sent={sent}  rate={sent_rate:.1f}/s  (waiting for results...)")

    # Collect results in parallel threads so we don't bias latency by sequential polling.
    # E2E is measured from Record.created_ns (dispatch time) to the last stage's
    # exit timestamp stored inside the record itself — same methodology as ring_blocking_sim.
    import concurrent.futures

    e2e_ms_list: list[float] = []
    failed = 0
    collect_deadline = time.monotonic() + pipeline.duration_s * 2

    def _collect_one(item: tuple[int, AsyncResult]) -> float | None:
        _, ar = item
        remaining = collect_deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            result_bytes = ar.get(timeout=min(remaining, 60.0))
            final_record: Record = pickle.loads(result_bytes)
            # Use record-internal timestamps for accurate E2E (dispatch → last stage exit)
            if final_record.stage_exits_ns:
                return (final_record.stage_exits_ns[-1] - final_record.created_ns) / 1e6
            return None
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(50, sent)) as pool:
        for e2e_ms in pool.map(_collect_one, results):
            if e2e_ms is not None:
                e2e_ms_list.append(e2e_ms)
            else:
                failed += 1

    received = len(e2e_ms_list)
    if e2e_ms_list:
        e2e_ms_list.sort()
        n = len(e2e_ms_list)
        p50 = e2e_ms_list[n // 2]
        p90 = e2e_ms_list[int(n * 0.9)]
        p99 = e2e_ms_list[int(n * 0.99)]
        mean = statistics.mean(e2e_ms_list)
    else:
        p50 = p90 = p99 = mean = float("nan")

    print(f"  received={received}/{sent}  failed={failed}")
    print(f"  E2E  P50={p50:.1f}ms  P90={p90:.1f}ms  P99={p99:.1f}ms  mean={mean:.1f}ms")

    return {
        "sent": sent, "received": received, "failed": failed,
        "e2e_p50": p50, "e2e_p90": p90, "e2e_p99": p99, "e2e_mean": mean,
    }


# ---------------------------------------------------------------------------
# CLI: worker + source modes
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Celery ring pipeline")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Run source (dispatch records, collect results)")
    run_p.add_argument("--rate",     type=float, default=10.0)
    run_p.add_argument("--duration", type=float, default=30.0)

    # Worker mode: just import this module and let Celery handle the rest.
    # Use: celery -A simulation.celery_ring worker -Q <queues> -c N
    sub.add_parser("worker-hint", help="Show worker start command")

    args = parser.parse_args()

    if args.cmd == "run":
        pipeline = PipelineConfig(
            stages=DEFAULT_PIPELINE.stages,
            rate_per_s=args.rate,
            duration_s=args.duration,
            name=f"rate={args.rate}/s",
        )
        run_experiment(pipeline)

    elif args.cmd == "worker-hint":
        queues = ",".join(s.name for s in DEFAULT_PIPELINE.stages)
        print(f"""
Start Celery workers with:

  cd research_ui
  celery -A simulation.celery_ring worker \\
    -Q {queues} \\
    -c 20 \\
    --loglevel=warning \\
    -n ring@%h

Then in another terminal:
  python -m simulation.celery_ring run --rate 10 --duration 30
""")
    else:
        parser.print_help()
