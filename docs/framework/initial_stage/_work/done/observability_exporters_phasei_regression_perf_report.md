# Phase I Regression/Perf Report

## Scope

Phase I closes the observability exporters + AsyncRunner rollout with:

- compatibility matrix verification (`runner_profile` x exporter backend),
- deterministic business-parity regression,
- trace-continuity regression,
- exporter-failure isolation gate,
- performance characterization snapshot.

## Matrix Result (`OBS-MAT-01`)

| Runner profile | Backend    | Expected | Result | Notes |
|---|---|---|---|---|
| `sync`  | `urllib`   | pass | pass | |
| `sync`  | `requests` | pass | pass | |
| `sync`  | `httpx`    | pass | pass | |
| `sync`  | `aiohttp`  | fail | fail | async-only backend (requires bridge) |
| `sync`  | `urllib3`  | pass | pass | |
| `sync`  | `grpcio`   | pass | pass | |
| `sync`  | `otel_sdk` | pass | pass | |
| `async` | `urllib`   | fail | fail | sync-only backend (requires bridge) |
| `async` | `requests` | fail | fail | sync-only backend (requires bridge) |
| `async` | `httpx`    | pass | pass | |
| `async` | `aiohttp`  | pass | pass | |
| `async` | `urllib3`  | fail | fail | sync-only backend (requires bridge) |
| `async` | `grpcio`   | fail | fail | sync-only backend (requires bridge) |
| `async` | `otel_sdk` | fail | fail | sync-only backend (requires bridge) |

## Regression Runs

### `OBS-MAT-02` (business parity)

Commands:

```bash
.venv/bin/pytest -q tests/integration/test_end_to_end_baseline_limits.py
.venv/bin/pytest -q tests/integration/test_end_to_end_experiment_features.py
.venv/bin/pytest -q tests/stream_kernel/execution/runtime
```

Result: pass

### `OBS-MAT-03` (trace continuity)

Commands:

```bash
.venv/bin/pytest -q tests/stream_kernel/observability/test_tracing_observer.py
.venv/bin/pytest -q tests/stream_kernel/execution/runtime/test_async_runner.py
.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py
```

Result: pass

### `OBS-MAT-04` (exporter failure isolation)

Commands:

```bash
.venv/bin/pytest -q tests/adapters/test_trace_sinks.py
.venv/bin/pytest -q tests/stream_kernel/observability/test_tracing_observer.py -k 'failure or fanout'
```

Result: pass

## Performance Snapshot

Command set:

```bash
.venv/bin/python - <<'PY'
from __future__ import annotations
import statistics, time
from stream_kernel.execution.runtime.runner import SyncRunner, AsyncRunner
from stream_kernel.integration.consumer_registry import InMemoryConsumerRegistry
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.integration.work_queue import InMemoryQueue
from stream_kernel.platform.services.state.context import InMemoryKvContextService
from stream_kernel.platform.services.observability import NoOpObservabilityService
from stream_kernel.routing.routing_service import RoutingService
from stream_kernel.routing.envelope import Envelope

N=2000

def bench_sync():
    queue=InMemoryQueue()
    for i in range(N):
        queue.push(Envelope(payload=i, target='n', trace_id=f't{i}'))
    runner=SyncRunner(
        nodes={'n': lambda payload, ctx: []},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
    )
    t0=time.perf_counter()
    runner.run()
    return time.perf_counter()-t0

async def node_async(payload, ctx):
    return []

def bench_async():
    queue=InMemoryQueue()
    for i in range(N):
        queue.push(Envelope(payload=i, target='n', trace_id=f't{i}'))
    runner=AsyncRunner(
        nodes={'n': node_async},
        work_queue=queue,
        context_service=InMemoryKvContextService(InMemoryKvStore()),
        router=RoutingService(registry=InMemoryConsumerRegistry(), strict=True),
        observability=NoOpObservabilityService(),
    )
    t0=time.perf_counter()
    runner.run()
    return time.perf_counter()-t0

sync_samples=[bench_sync() for _ in range(5)]
async_samples=[bench_async() for _ in range(5)]
print('sync', f'avg_ms={statistics.mean(sync_samples)*1000:.2f}', f'p95_ms={statistics.quantiles(sync_samples, n=100)[94]*1000:.2f}')
print('async', f'avg_ms={statistics.mean(async_samples)*1000:.2f}', f'p95_ms={statistics.quantiles(async_samples, n=100)[94]*1000:.2f}')
PY
```

Results:

- `sync`:
  - `avg_ms=4.08`
  - `p95_ms=5.70`
- `async`:
  - `avg_ms=11.75`
  - `p95_ms=12.77`

## Sign-off

- [x] Matrix regression complete
- [x] Unsupported combinations documented with deterministic validation reason
- [x] Performance snapshot recorded
- [x] Master plan Phase I marked complete
