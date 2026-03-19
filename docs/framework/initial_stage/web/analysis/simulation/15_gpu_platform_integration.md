# GPU Cluster Scheduling — Platform Integration Architecture

**Date:** 2026-03-17
**Context:** Experiment Series E (document 14) defines three GPU scheduling experiments.
This document answers the prior question: *is the stream_kernel framework the right tool
for orchestrating GPU workers, and how do all the pieces fit together?*

Short answer: yes — the framework covers everything except a GPU Registry, without
architectural compromise. GPU workers, the autoscaler, and the downstream business
pipeline are all instances of the same `AsyncRunner` pattern, connected by the same
Celery+Redis transport, observed through the same `ObservabilityService` hooks.

---

## 1. What the Framework Already Solves

### GPU worker = leaf node (established in D-6)

The `@worker_process_init` + `AsyncRunner` pattern from D-6 maps directly onto a GPU
worker. The only difference from a CPU leaf is that startup includes model loading:

```
@worker_process_init:
  1. download / verify model weights  (S3 or local cache)
  2. load weights into VRAM
  3. register worker state in GPU Registry
  4. start AsyncRunner in background thread
       ├─ GPUBatcherNode       ← gpu_stage.run_batcher() from Series C
       ├─ BusinessResultSink   ← pushes result to downstream Celery queue
       └─ ObservabilityService ← traces, metrics, logs
```

The cold-start phase (steps 1–3) runs entirely inside `@worker_process_init`, before
the first Celery task is accepted. The framework's existing lifecycle model handles this
without modification.

### Observability: full coverage without new code

`ObservabilityService.before_node()` / `after_node()` already capture timing at every
node boundary. For GPU workers, the `SpanEvent` is extended with GPU-specific fields:

```python
@dataclass
class GPUSpanEvent(SpanEvent):
    batch_size:      int   = 1
    vram_used_mb:    int   = 0
    gpu_utilisation: float = 0.0   # from nvidia-ml-py (pynvml)
    model_id:        str   = ""
    cold_start:      bool  = False
    cold_start_ms:   float = 0.0
```

This flows through the existing `TraceSinkPort` → `OTelOtlpTraceSink` → Jaeger/Tempo
pipeline. No changes to `ObservabilityService` or any sink adapter.

One `trace_id` spans the entire request lifecycle:

```
[broker enqueue span]
  → [batch wait span]        GPU worker, batch assembly
    → [inference span]       GPU worker, forward pass
      → [result deliver span] downstream pipeline receives result
```

A Jaeger waterfall shows exactly where each request spent time — waiting in queue,
waiting for batch fill, or executing on GPU.

### Audit trail: built-in

`ContextService` carries `trace_id` through every node. Every autoscaling decision is
emitted as a structured trace event via the same `publish_trace()` path already used
for business events:

```python
observability.publish_trace(
    event=ScaleDecisionEvent(
        decision      = "scale_up",
        trigger       = "queue_depth_exceeded",
        queue_depth   = 47,
        current_gpus  = 2,
        target_gpus   = 3,
        cost_estimate = 0.40,   # $/h for new instance
    ),
    trace_id = decision_trace_id,
)
```

Routing this to a `JsonlTraceSink` backed by an append-only Postgres table gives a
fully immutable audit log: every scale decision is reproducible from first principles
(queue depth, worker count, policy version, timestamp).

---

## 2. New Components Required

Only three additions are needed beyond what the framework already provides.

### 2a. GPU Registry — central cluster state

The only genuinely new data structure. Tracks live worker state, VRAM allocation, and
queue depths across the cluster:

```python
@dataclass
class GPUWorkerState:
    worker_id:     str
    instance_id:   str     # RunPod / cloud instance identifier
    model_id:      str
    vram_total_mb: int
    vram_used_mb:  int
    status:        str     # "cold_start" | "ready" | "busy" | "draining"
    queue_depth:   int     # live read from Redis queue length
    started_at:    float
    spot:          bool

# Storage: Redis hash (small, frequently updated)
# TTL: 30s; workers refresh every 5s (heartbeat)
# On heartbeat miss: mark "unreachable", trigger autoscaler evaluation
```

The Registry is intentionally stateless between restarts — it is always reconstructed
from live heartbeats. No persistence required; durability lives at the task-queue level.

### 2b. Autoscaler Node — a control-plane async node

A standard async node inside a dedicated control-plane `AsyncRunner`. It receives a
`ClusterSnapshot` (assembled by upstream monitoring nodes), evaluates the configured
policy, and emits commands:

```python
class AutoscalerNode:
    def __init__(self, policy: AutoscalerPolicy) -> None:
        self._policy = policy

    async def __call__(
        self, payload: ClusterSnapshot, ctx: dict
    ) -> list:
        decision = self._policy.evaluate(payload)
        if decision.action == "scale_up":
            return [SpawnWorkerCommand(
                model_id = decision.model_id,
                spot     = decision.use_spot,
                reason   = decision.reason,
            )]
        if decision.action == "scale_down":
            return [DrainWorkerCommand(
                worker_id = decision.target_worker,
                reason    = decision.reason,
            )]
        return []
```

`AutoscalerPolicy` is a plain strategy object — swappable without touching the node.
Three policies correspond directly to the E-3 experiment variants:

```python
class ReactivePolicy(AutoscalerPolicy):
    # scale up when queue_depth > K; scale down after T seconds idle

class PredictivePolicy(AutoscalerPolicy):
    # EWMA of arrival rate × estimated drain time → proactive spawn

class PreWarmPolicy(AutoscalerPolicy):
    # maintain N spare cold instances; fill from spot pool
```

### 2c. RunPod Adapter Node — side-effectful async node

Receives `SpawnWorkerCommand` / `DrainWorkerCommand`, calls the RunPod API with
idempotency and retry, emits a `WorkerLifecycleEvent` for the audit trail:

```python
class RunPodAdapterNode:
    async def __call__(
        self, payload: SpawnWorkerCommand | DrainWorkerCommand, ctx: dict
    ) -> list:
        if isinstance(payload, SpawnWorkerCommand):
            instance = await self._api.create_pod(
                model_id   = payload.model_id,
                gpu_type   = self._select_gpu(payload),
                spot       = payload.spot,
                idempotency_key = ctx["__trace_id"],  # RunPod supports this
            )
            return [WorkerLifecycleEvent(
                action      = "spawned",
                instance_id = instance.id,
                model_id    = payload.model_id,
                cost_per_h  = instance.cost_per_h,
                spot        = payload.spot,
            )]
        # DrainWorkerCommand: call runner.request_stop() via control channel
        ...
```

`WorkerLifecycleEvent` is routed to the audit `TraceSink` — every instance spawn and
termination is permanently recorded with full context.

---

## 3. Control Plane Architecture

The autoscaler and GPU registry monitoring run as a separate `AsyncRunner` process —
the same control-plane pattern already used in `execution/orchestration/lifecycle/`:

```
Control Plane Process (AsyncRunner, runs continuously)
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  node:queue_monitor    reads Redis queue depths every 1s         │
│       │ QueueSnapshot                                            │
│  node:registry_reader  reads GPU worker heartbeats from Redis    │
│       │ RegistrySnapshot                                         │
│  node:snapshot_merger  joins queue + registry → ClusterSnapshot  │
│       │ ClusterSnapshot                                          │
│  node:autoscaler       evaluates policy → scale commands         │
│       │ SpawnWorkerCommand | DrainWorkerCommand | NoOp           │
│  node:runpod_adapter   executes RunPod API calls                 │
│       │ WorkerLifecycleEvent                                     │
│  sink:audit_trail      append-only JSONL / Postgres              │
│                                                                  │
│  DispatchingObservabilityService                                 │
│    ├─ TraceSink  → OTelOtlpTraceSink (Jaeger)                    │
│    ├─ MetricSink → Prometheus (queue depth, worker count, cost)  │
│    └─ LogSink    → structured log (policy decisions, API errors) │
└──────────────────────────────────────────────────────────────────┘
```

This is architecturally identical to the existing root-process orchestration. The
control plane sends commands to leaf (GPU) workers through the existing lifecycle
signalling channel, not through a new protocol.

---

## 4. Full System Integration Picture

```
                    ┌────────────────────────────────────────┐
                    │  Control Plane (AsyncRunner)            │
                    │  QueueMonitor → Registry → Autoscaler  │
                    │  → RunPodAdapter → AuditSink           │
                    └──────────┬─────────────────────────────┘
                               │ spawn / drain via RunPod API
               ┌───────────────┼────────────────────────────┐
               ▼               ▼                            ▼
      ┌──────────────┐ ┌──────────────┐           ┌──────────────┐
      │ GPU Worker 1  │ │ GPU Worker 2  │    ...    │ GPU Worker N  │
      │ (AsyncRunner) │ │ (AsyncRunner) │           │ (AsyncRunner) │
      │              │ │              │           │              │
      │ MiniLM 200MB │ │ BGE-large    │           │ Phi-3-mini   │
      │              │ │ 800MB        │           │ 2.3GB        │
      │ GPUBatcher   │ │ GPUBatcher   │           │ GPUBatcher   │
      │ ResultSink   │ │ ResultSink   │           │ ResultSink   │
      │ ObsSvc ──────┤ │ ObsSvc ──────┤           │ ObsSvc ──────┤
      └──────┬───────┘ └──────┬───────┘           └──────┬───────┘
             │                │                          │
             │  heartbeats → Redis GPU Registry          │
             │                │                          │
             └────────────────┴──────────────────────────┘
                              │
                  ┌───────────▼──────────────┐
                  │   Redis Broker (Celery)   │
                  │   + Result Backend        │
                  │   + GPU Registry hashes   │
                  └───────────┬──────────────┘
                              │ results delivered to
                  ┌───────────▼──────────────────────────┐
                  │  Business Pipeline (AsyncRunner)      │
                  │                                       │
                  │  IngressNode   ← raw request          │
                  │  EmbeddingNode ← calls GPU worker     │
                  │  RerankNode    ← calls GPU worker     │
                  │  ValidateNode                         │
                  │  LedgerSinkNode                       │
                  │  ObsSvc → same trace_id throughout    │
                  └───────────────────────────────────────┘
```

A single `trace_id` is minted at the business pipeline's `IngressNode` and carried
through every hop: broker enqueue, GPU batch wait, GPU inference, result delivery,
business validation, ledger write. One Jaeger trace = one complete request audit.

---

## 5. Durability Model

| layer | storage | guarantee | recovery on failure |
|-------|---------|-----------|---------------------|
| Task queue | Redis + Celery `acks_late=True` | at-least-once | broker restart → replay from AOF |
| Model weights | S3 / shared EFS | durable source of truth | worker restarts → re-download |
| VRAM state | worker process memory | ephemeral | crash → cold restart, Celery retries task |
| GPU Registry | Redis (heartbeat TTL 30s) | best-effort | rebuilt from live heartbeats on restart |
| Audit trail | append-only Postgres / JSONL | immutable | Postgres WAL; no recovery needed |
| RunPod instances | RunPod API (idempotent) | cloud-managed | idempotency key = trace_id; safe to retry |
| Autoscaler state | stateless (derived from queue + registry) | none needed | re-evaluates on each tick |

**The core durability guarantee:** VRAM state is intentionally not durable. A GPU worker
crash causes a cold-start penalty on restart, but no data loss — Celery's `acks_late=True`
ensures in-flight tasks are requeued automatically. This is the same recovery model as
`ring_blocking_sim`'s leaf restart: the leaf crashes, the root retransmits.

---

## 6. What Is Genuinely New vs What Already Exists

| concern | mechanism | status |
|---------|-----------|--------|
| GPU worker lifecycle | `@worker_process_init` + `AsyncRunner` | **already built** (D-6) |
| Batch assembly | `gpu_stage.run_batcher()` background asyncio task | **already built** (Series C) |
| Per-request tracing | `ObservabilityService` → `OTelOtlpTraceSink` | **already built** |
| Audit trail for decisions | `publish_trace()` → append-only sink | **already built** |
| Control-plane orchestration | separate `AsyncRunner` process | **already built** (platform lifecycle) |
| GPU Registry | Redis hash + heartbeat | **new** — small, self-healing |
| Autoscaler Node | async node with pluggable policy | **new node**, existing mechanism |
| RunPod Adapter Node | async node with HTTP client + retry | **new node**, existing mechanism |
| Cost model | derived from `WorkerLifecycleEvent` stream | **new** — computed from audit trail |
| `GPUSpanEvent` | extends `SpanEvent` with GPU fields | **new** — minimal, additive |

The GPU Registry is the only component that does not fit neatly into an existing
abstraction. Everything else is a new node implementation inside the existing runner,
router, and observability infrastructure.

---

## 7. How a Request Flows End-to-End

Taking a semantic search request in a RAG pipeline as a concrete example:

```
1. HTTP request arrives at API gateway
   → minted trace_id = "abc-123"
   → published to Celery queue "pipeline.ingest"

2. Business Pipeline Worker picks up task
   → AsyncRunner: IngressNode seeds context(trace_id="abc-123")
   → EmbeddingNode: calls process_embedding.apply_async(text, trace_id="abc-123")
     published to Celery queue "gpu.embedding.miniml"

3. GPU Worker (MiniLM) picks up embedding task
   → Celery task injects Envelope into worker's AsyncRunner
   → GPUBatcherNode: waits up to 10ms for batch, assembles batch=8
   → forward pass: 4ms
   → ResultSink: resolves future, result published to "pipeline.embedding_results"
   → ObsSvc emits:
       SpanEvent(node="gpu_batcher", duration=14ms, batch_size=8, trace_id="abc-123")
       GPUMetric(utilisation=0.72, vram_used=210MB, trace_id="abc-123")

4. Business Pipeline Worker receives embedding result
   → RerankNode: calls process_rerank.apply_async(candidates, trace_id="abc-123")
   [same pattern, different GPU worker queue]

5. ValidateNode, LedgerSinkNode complete
   → final ObsSvc span closes trace "abc-123"
   → Jaeger: full waterfall from HTTP ingress to ledger write
   → Postgres audit: every node, every GPU call, every batch decision
```

Total observable hops for trace "abc-123": 12 spans across 3 process boundaries
(business pipeline → GPU embedding worker → business pipeline → GPU rerank worker →
business pipeline). All correlated by the same `trace_id`, all emitted through the
same `TraceSinkPort` interface without any request-specific wiring.

---

## 8. Connection to Experiment Series E

Each of the three E-experiments maps to a specific part of this architecture:

| experiment | validates | platform component exercised |
|------------|-----------|------------------------------|
| **E-1** Model packing | how many models fit on one GPU without SLA degradation | `GPUBatcherNode`, `GPURegistry` VRAM tracking |
| **E-2** Heterogeneous scheduler | GPU type affinity vs work-stealing under mixed load | `AutoscalerNode` policy, router type-affinity |
| **E-3** RunPod autoscaling | cost × SLA tradeoff under bursty load | `RunPodAdapterNode`, `PreWarmPolicy`, audit trail cost accounting |

The simulation (`ring_blocking_sim.py` extended) models the control-plane `AsyncRunner`
and GPU leaf workers as real OS processes — the same architecture described here, with
`asyncio.sleep()` standing in for actual GPU forward passes and `requests.post()` calls
substituting for the RunPod API. Moving to production is a matter of replacing those
two stubs with real implementations; the orchestration, observability, and durability
layers are unchanged.
