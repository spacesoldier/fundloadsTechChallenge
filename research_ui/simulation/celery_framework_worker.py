"""
Celery + AsyncRunner integration simulation.

This module demonstrates how the stream_kernel AsyncRunner runs *inside* a Celery
prefork worker process. Each Celery task is not self-contained logic — it is a thin
bridge that injects a record into the worker-level AsyncRunner and blocks until the
runner's result sink resolves the answer.

Architecture per prefork worker process
----------------------------------------

  Celery prefork child process
  ┌────────────────────────────────────────────────────────────────────────┐
  │  background thread: asyncio event loop (worker lifetime)               │
  │  ┌──────────────────────────────────────────────────────────────────┐  │
  │  │  AsyncRunner.run_until_stopped_async()                           │  │
  │  │                                                                  │  │
  │  │  CeleryHandoffQueue ←─── push(envelope) from Celery task thread  │  │
  │  │         │ pop()                                                  │  │
  │  │         ▼                                                        │  │
  │  │  node:enrich_fund_data  (async, simulates DB lookup)             │  │
  │  │         │ FundLoadResult                                         │  │
  │  │         ├──────────────────────────────────────────────────────► │  │
  │  │  sink:write_ledger  (business sink — DB write sim)               │  │
  │  │         │                                                        │  │
  │  │  sink:result  (CeleryResultSinkNode)                             │  │
  │  │    future[trace_id].set_result(result)                           │  │
  │  │         │                                                        │  │
  │  │  ObservabilityService                                            │  │
  │  │    before_node / after_node → SpanEvent → JSONL + stdout sink    │  │
  │  └──────────────────────────────────────────────────────────────────┘  │
  │                                        │                               │
  │  @app.task process_fund_load():        │                               │
  │    1. create concurrent.futures.Future │                               │
  │    2. seed context                     │                               │
  │    3. queue.push(envelope) ───────────►│                               │
  │    4. fut.result(timeout=30) ◄─────────┘                               │
  │    5. return result bytes                                              │
  └────────────────────────────────────────────────────────────────────────┘

Observability wiring
--------------------
The AsyncRunner fires before_node / after_node on every node execution.
CelerySpanObservabilityService converts these into SpanEvents:

  before_node  →  span.start  (tag: node_name, trace_id, payload metadata)
  after_node   →  span.finish (tag: duration_ms, output types)
  on_node_error → span.error  (tag: error type, message)

SpanEvents are emitted to two sinks configured at worker startup:
  1. JsonlTraceSink → /tmp/celery_framework_traces.jsonl
  2. StdoutTraceSink → stderr (for visibility during dev)

Both sinks implement TraceSinkPort from the platform contracts — the same sinks
used in production. Swapping to OTelOtlpTraceSink (Jaeger/OTLP) requires only
changing the sink at wiring time, not the observability service.

Running
-------
  # Terminal 1 — start workers:
  cd research_ui
  PYTHONPATH=. celery -A simulation.celery_framework_worker worker \\
    -Q fund_load -c 4 --loglevel=warning -n framework@localhost

  # Terminal 2 — send records:
  PYTHONPATH=. python -m simulation.celery_framework_worker run \\
    --rate 5 --duration 30
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import pickle
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from celery.result import AsyncResult

# ---------------------------------------------------------------------------
# Bootstrap sys.path so stream_kernel is importable inside the worker
# (in production this would be a proper package install)
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stream_kernel.execution.runtime.runner import AsyncRunner
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.routing.router import Router
from stream_kernel.routing.envelope import Envelope

from simulation.record import Record
from simulation.fund_load_domain import FundLoadRecord, FundLoadResult

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------
BROKER_URL  = os.environ.get("CELERY_BROKER",  "redis://localhost:6379/1")
BACKEND_URL = os.environ.get("CELERY_BACKEND", "redis://localhost:6379/2")

app = Celery("celery_framework_worker", broker=BROKER_URL, backend=BACKEND_URL)
app.conf.update(
    task_serializer   = "pickle",
    result_serializer = "pickle",
    accept_content    = ["pickle"],
    task_acks_late    = True,
    worker_prefetch_multiplier = 1,
    result_expires    = 300,
)


# ---------------------------------------------------------------------------
# Observability: SpanEvent + CelerySpanObservabilityService
# ---------------------------------------------------------------------------

@dataclass
class SpanEvent:
    """One node execution span — emitted to trace sinks."""
    trace_id:    str
    node_name:   str
    event:       str          # "start" | "finish" | "error"
    duration_ms: float | None = None
    payload_type: str         = ""
    output_types: list[str]   = field(default_factory=list)
    error:        str | None  = None
    worker_pid:  int          = field(default_factory=os.getpid)
    ts_ns:       int          = field(default_factory=time.monotonic_ns)


class CelerySpanObservabilityService(NoOpObservabilityService):
    """
    Extends NoOpObservabilityService with real span emission.
    Implements ObservabilityPipelineService protocol.

    before_node  → records start time (returned as opaque state)
    after_node   → computes duration, emits SpanEvent to trace sinks
    on_node_error → emits error SpanEvent
    on_run_end   → flushes all sinks
    """

    def __init__(self, trace_sinks: list[Any]) -> None:
        self._sinks = trace_sinks

    def before_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict,
        trace_id: str | None,
    ) -> object | None:
        # Return start time as opaque state — received back in after_node.
        return time.monotonic_ns()

    def after_node(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict,
        trace_id: str | None,
        outputs: list[object],
        state: object | None,   # start_ns from before_node
    ) -> None:
        start_ns = state if isinstance(state, int) else time.monotonic_ns()
        duration_ms = (time.monotonic_ns() - start_ns) / 1e6
        event = SpanEvent(
            trace_id    = trace_id or "",
            node_name   = node_name,
            event       = "finish",
            duration_ms = round(duration_ms, 3),
            payload_type = type(payload).__name__,
            output_types = [type(o).__name__ for o in outputs],
        )
        self._emit(event)

    def on_node_error(
        self,
        *,
        node_name: str,
        payload: object,
        ctx: dict,
        trace_id: str | None,
        error: Exception,
        state: object | None,
    ) -> None:
        start_ns = state if isinstance(state, int) else time.monotonic_ns()
        duration_ms = (time.monotonic_ns() - start_ns) / 1e6
        event = SpanEvent(
            trace_id    = trace_id or "",
            node_name   = node_name,
            event       = "error",
            duration_ms = round(duration_ms, 3),
            payload_type = type(payload).__name__,
            error       = f"{type(error).__name__}: {error}",
        )
        self._emit(event)

    def on_run_end(self) -> None:
        for sink in self._sinks:
            try:
                sink.flush()
            except Exception:
                pass

    def _emit(self, event: SpanEvent) -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Trace sinks — both implement TraceSinkPort (emit / flush / close)
# ---------------------------------------------------------------------------

class JsonlTraceSink:
    """Appends SpanEvents as newline-delimited JSON to a log file."""
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def emit(self, record: object) -> None:
        if not isinstance(record, SpanEvent):
            return
        line = json.dumps({
            "ts_ns":       record.ts_ns,
            "worker_pid":  record.worker_pid,
            "trace_id":    record.trace_id,
            "node_name":   record.node_name,
            "event":       record.event,
            "duration_ms": record.duration_ms,
            "payload_type": record.payload_type,
            "output_types": record.output_types,
            "error":       record.error,
        })
        with self._lock:
            with self._path.open("a") as f:
                f.write(line + "\n")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class StdoutTraceSink:
    """Prints a compact span summary to stderr for dev visibility."""
    def emit(self, record: object) -> None:
        if not isinstance(record, SpanEvent):
            return
        pid = record.worker_pid
        tid = record.trace_id[:8]
        node = record.node_name
        evt  = record.event
        dur  = f"{record.duration_ms:.1f}ms" if record.duration_ms is not None else "-"
        err  = f" ERROR={record.error}" if record.error else ""
        print(f"  [pid={pid}] span {evt:7s} node={node:<30s} dur={dur:>8s} trace={tid}{err}",
              file=sys.stderr, flush=True)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Business nodes (callable: (payload, ctx) -> list[output])
# ---------------------------------------------------------------------------

class EnrichFundDataNode:
    """
    Async node: enriches FundLoadRecord with ledger data.
    In production: asyncpg query to lookup account limits.
    Simulation: lognormal sleep to model DB latency.
    """
    def __init__(self, mean_ms: float = 10.0, sigma: float = 0.5) -> None:
        import math, numpy as np
        self._rng = np.random.default_rng()
        self._mu  = math.log(mean_ms)
        self._sigma = sigma

    async def __call__(self, payload: FundLoadRecord, ctx: dict) -> list:
        # Simulate async DB lookup
        latency_ms = float(self._rng.lognormal(self._mu, self._sigma))
        await asyncio.sleep(latency_ms / 1000.0)
        # Return enriched result
        ledger_id = f"LDG-{payload.account_id}-{payload.seq:06d}"
        result = FundLoadResult(
            seq        = payload.seq,
            account_id = payload.account_id,
            amount_usd = payload.amount_usd,
            ledger_id  = ledger_id,
            status     = "accepted" if payload.amount_usd <= 1_000_000 else "rejected",
        )
        return [result]


class ValidateRulesNode:
    """
    Sync node: validates business rules on FundLoadResult.
    In production: rule engine lookup, AML checks.
    Simulation: instant validation with deterministic reject logic.
    """
    def __call__(self, payload: FundLoadResult, ctx: dict) -> list:
        if payload.amount_usd <= 0:
            payload.status = "rejected"
        return [payload]


class LedgerSinkNode:
    """
    Business sink: writes FundLoadResult to the ledger.
    In production: asyncpg INSERT into ledger table.
    Simulation: records into an in-memory list (visible to the test harness).
    """
    def __init__(self) -> None:
        self.records: list[FundLoadResult] = []
        self._lock = threading.Lock()

    async def __call__(self, payload: FundLoadResult, ctx: dict) -> list:
        await asyncio.sleep(0.002)  # 2ms simulate write latency
        with self._lock:
            self.records.append(payload)
        return []   # sink: no outputs


class CeleryResultSinkNode:
    """
    Platform result sink: resolves the pending concurrent.futures.Future
    so the Celery task function can return the result to its caller.

    This is the bridge from the async runner back to the sync Celery task.
    In production, this sink would also push to a reply queue or WebSocket.
    """
    def __init__(self, pending: dict[str, concurrent.futures.Future]) -> None:
        self._pending = pending

    def __call__(self, payload: FundLoadResult, ctx: dict) -> list:
        trace_id = ctx.get("__trace_id")
        if not isinstance(trace_id, str):
            return []
        fut = self._pending.get(trace_id)
        if fut is not None and not fut.done():
            fut.set_result(payload)
        return []   # sink: no outputs


# ---------------------------------------------------------------------------
# Worker-level state (one per prefork child process)
# ---------------------------------------------------------------------------

_worker_loop:       asyncio.AbstractEventLoop | None = None
_work_queue:        InMemoryQueue | None             = None
_context_service:   InMemoryKvContextService | None  = None
_runner_thread:     threading.Thread | None          = None
_pending_futures:   dict[str, concurrent.futures.Future] = {}
_ledger_sink:       LedgerSinkNode | None            = None


def _build_runner(
    work_queue:      InMemoryQueue,
    context_service: InMemoryKvContextService,
    ledger_sink:     LedgerSinkNode,
    pending:         dict[str, concurrent.futures.Future],
) -> AsyncRunner:
    """
    Constructs the AsyncRunner with all platform components wired.

    nodes           — business logic + sink nodes
    router          — type-based fan-out: FundLoadRecord → enrich, FundLoadResult → validate/ledger/result
    context_service — in-memory KV (per-worker; production uses shared Redis)
    observability   — span tracing to JSONL + stdout
    """
    trace_sinks = [
        JsonlTraceSink(Path("/tmp/celery_framework_traces.jsonl")),
        StdoutTraceSink(),
    ]
    observability = CelerySpanObservabilityService(trace_sinks)

    enrich_node   = EnrichFundDataNode(mean_ms=10.0, sigma=0.5)
    validate_node = ValidateRulesNode()
    result_sink   = CeleryResultSinkNode(pending)

    router = Router(
        consumers={
            FundLoadRecord: ["node:enrich_fund_data"],
            FundLoadResult: ["node:validate_rules", "sink:write_ledger", "sink:result"],
        },
        strict=True,
    )

    return AsyncRunner(
        nodes={
            "node:enrich_fund_data": enrich_node,
            "node:validate_rules":   validate_node,
            "sink:write_ledger":     ledger_sink,
            "sink:result":           result_sink,
        },
        work_queue     = work_queue,
        router         = router,
        context_service= context_service,
        observability  = observability,
        run_id             = f"celery_worker_pid{os.getpid()}",
        scenario_id        = "fund_load",
        drain_on_stop      = True,
        # sink:result needs __trace_id to resolve the pending future
        full_context_nodes = {"sink:result"},
    )


# ---------------------------------------------------------------------------
# Celery worker lifecycle signals
# ---------------------------------------------------------------------------

@worker_process_init.connect
def _on_worker_process_init(**kwargs: object) -> None:
    """
    Called once per prefork child process at startup.
    Starts the AsyncRunner event loop in a background daemon thread.
    """
    global _worker_loop, _work_queue, _context_service, _runner_thread, _ledger_sink

    _work_queue      = InMemoryQueue()
    _context_service = InMemoryKvContextService(store=InMemoryKvStore())
    _ledger_sink     = LedgerSinkNode()

    runner = _build_runner(
        work_queue      = _work_queue,
        context_service = _context_service,
        ledger_sink     = _ledger_sink,
        pending         = _pending_futures,
    )

    _worker_loop = asyncio.new_event_loop()

    def _run_loop() -> None:
        asyncio.set_event_loop(_worker_loop)
        _worker_loop.run_until_complete(
            runner.run_until_stopped_async(poll_timeout_seconds=0.005)
        )

    _runner_thread = threading.Thread(target=_run_loop, name="AsyncRunner", daemon=True)
    _runner_thread.start()
    print(f"[pid={os.getpid()}] AsyncRunner started", file=sys.stderr, flush=True)


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**kwargs: object) -> None:
    """Graceful stop: signals runner to drain then exit."""
    print(f"[pid={os.getpid()}] AsyncRunner stopping", file=sys.stderr, flush=True)
    # The runner checks _stop_requested; we signal via the module-level flag.
    # In production: call runner.request_stop() — requires keeping runner in module scope.
    # Here we rely on daemon thread dying with the process.


# ---------------------------------------------------------------------------
# Celery task: the thin sync bridge
# ---------------------------------------------------------------------------

@app.task(name="process_fund_load", queue="fund_load", acks_late=True)
def process_fund_load(payload_bytes: bytes) -> bytes:
    """
    Entry point for each fund-load record.

    1. Deserialise FundLoadRecord from broker bytes.
    2. Create a concurrent.futures.Future registered by trace_id.
    3. Seed the context service (so runner nodes see trace metadata).
    4. Push an Envelope into the AsyncRunner's work queue (thread-safe).
    5. Block until CeleryResultSinkNode resolves the future.
    6. Return serialised FundLoadResult.

    The Celery task holds a prefork worker slot for the duration — this is
    equivalent to the ring_blocking_sim leaf holding an asyncio.Semaphore slot.
    For high concurrency, -c (prefork count) is the dial, same as pool_size.
    """
    if _work_queue is None or _context_service is None or _worker_loop is None:
        raise RuntimeError("Worker not initialised — @worker_process_init did not fire")

    record: FundLoadRecord = pickle.loads(payload_bytes)
    trace_id = record.trace_id

    # Register future for this trace
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _pending_futures[trace_id] = fut

    try:
        # Seed context (thread-safe: InMemoryKvStore uses plain dict)
        _context_service.seed(
            trace_id    = trace_id,
            payload     = record,
            run_id      = f"celery_worker_pid{os.getpid()}",
            scenario_id = "fund_load",
        )

        # Inject into runner work queue (thread-safe: InMemoryQueue uses Condition)
        envelope = Envelope(
            payload  = record,
            target   = "node:enrich_fund_data",
            trace_id = trace_id,
        )
        _work_queue.push(envelope)

        # Block until runner produces result (or timeout)
        result: FundLoadResult = fut.result(timeout=30.0)
        return pickle.dumps(result)

    finally:
        _pending_futures.pop(trace_id, None)


# ---------------------------------------------------------------------------
# Source: dispatch records and collect results
# ---------------------------------------------------------------------------

def run_experiment(rate_per_s: float = 5.0, duration_s: float = 30.0) -> None:
    """
    Dispatch FundLoadRecord tasks at the given rate and collect results.
    Measures E2E latency from FundLoadRecord.created_ns to FundLoadResult.processing_ns.
    """
    interval = 1.0 / rate_per_s
    end_time = time.monotonic() + duration_s

    print(f"\n{'─'*70}")
    print(f"  Celery+AsyncRunner pipeline: fund_load")
    print(f"  rate={rate_per_s}/s  duration={duration_s}s  broker={BROKER_URL}")
    print(f"{'─'*70}")

    pending: list[tuple[FundLoadRecord, AsyncResult]] = []
    seq = 0
    dispatch_start = time.monotonic()

    while time.monotonic() < end_time:
        record = FundLoadRecord(
            seq        = seq,
            account_id = f"ACC-{seq % 100:04d}",
            amount_usd = 500_000.0 + (seq % 50) * 10_000,
        )
        ar = process_fund_load.apply_async(
            args   = [pickle.dumps(record)],
            queue  = "fund_load",
        )
        pending.append((record, ar))
        seq += 1

        next_send  = dispatch_start + seq * interval
        sleep_for  = next_send - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    sent      = seq
    sent_rate = sent / (time.monotonic() - dispatch_start)
    print(f"  sent={sent}  rate={sent_rate:.1f}/s  (collecting...)")

    # Collect results in parallel
    e2e_ms:  list[float] = []
    failed               = 0
    deadline             = time.monotonic() + duration_s * 2

    def _get(item: tuple[FundLoadRecord, AsyncResult]) -> float | None:
        src, ar = item
        rem = deadline - time.monotonic()
        if rem <= 0:
            return None
        try:
            result_bytes = ar.get(timeout=min(rem, 60.0))
            result: FundLoadResult = pickle.loads(result_bytes)
            return (result.processing_ns - src.created_ns) / 1e6
        except Exception:
            return None

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=min(100, sent)) as pool:
        for ms in pool.map(_get, pending):
            if ms is not None:
                e2e_ms.append(ms)
            else:
                failed += 1

    received = len(e2e_ms)
    if e2e_ms:
        e2e_ms.sort()
        n = len(e2e_ms)
        p50 = e2e_ms[n // 2]
        p90 = e2e_ms[int(n * 0.9)]
        p99 = e2e_ms[int(n * 0.99)]
        mean = statistics.mean(e2e_ms)
    else:
        p50 = p90 = p99 = mean = float("nan")

    print(f"  received={received}/{sent}  failed={failed}")
    print(f"  E2E  P50={p50:.1f}ms  P90={p90:.1f}ms  P99={p99:.1f}ms  mean={mean:.1f}ms")
    print(f"  traces → /tmp/celery_framework_traces.jsonl")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Celery+AsyncRunner fund-load pipeline")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run")
    run_p.add_argument("--rate",     type=float, default=5.0)
    run_p.add_argument("--duration", type=float, default=30.0)

    sub.add_parser("worker-hint")
    args = parser.parse_args()

    if args.cmd == "run":
        run_experiment(rate_per_s=args.rate, duration_s=args.duration)

    elif args.cmd == "worker-hint":
        print("""
Start workers:
  cd research_ui
  PYTHONPATH=. celery -A simulation.celery_framework_worker worker \\
    -Q fund_load -c 4 --loglevel=warning -n framework@localhost

Then send records:
  PYTHONPATH=. python -m simulation.celery_framework_worker run \\
    --rate 5 --duration 30

Traces written to /tmp/celery_framework_traces.jsonl
""")
    else:
        parser.print_help()
