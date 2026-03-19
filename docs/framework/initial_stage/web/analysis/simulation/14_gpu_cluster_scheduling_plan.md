# Experiment E — GPU Cluster Scheduling for Heterogeneous Inference Workloads

**Date:** 2026-03-17
**Context:** Series D established that Celery+AsyncRunner handles CPU/DB/API pipelines
efficiently. The next frontier is GPU inference scheduling: how to keep GPU hardware
utilised when requests are mixed (tiny embeddings, mid-size rerankers, large generative
models), when models outnumber available GPUs, and when cold-start costs make ephemeral
instances expensive.

---

## 1. GPU Memory and Model Size Landscape (2026)

Understanding what fits where is the prerequisite for any scheduling discussion.

### Model weight sizes by class

| class | representative models | VRAM fp16 | VRAM int4/int8 | fits in 8 GB? |
|-------|-----------------------|-----------|----------------|---------------|
| **tiny encoder** | MiniLM-L6, BGE-small, DistilBERT | 60–300 MB | — | 20–100× |
| **medium encoder** | BGE-large, E5-large, CLIP-ViT-L | 0.6–1.5 GB | — | 5–12× |
| **small LLM** | LLaMA-3.2-1B, Phi-3-mini, Gemma-2B | 2–4 GB | 0.8–1.5 GB | 2–4× (quant) |
| **mid LLM** | Mistral-7B, LLaMA-3.1-8B | 14–16 GB | 4–5 GB | 1× (quant only) |
| **large LLM** | LLaMA-3.1-70B | 140 GB | 35–40 GB | multi-GPU only |
| **frontier** | LLaMA-3.1-405B, GPT-4-scale | 800 GB+ | 200+ GB | cluster only |

The **~200 MB** models that appear frequently in production requests are almost always
**embedding models** (semantic search, RAG retrieval, classification) or lightweight
vision models (OCR, image classifiers). A single 8 GB consumer GPU (RTX 3070, 4060 Ti)
can hold 20+ of these simultaneously with room to spare.

### The idle VRAM problem

A naïve one-model-per-GPU deployment wastes:

```
8 GB GPU, 200 MB model loaded:
  model weights:  0.2 GB   (2.5%)
  activations:    0.1 GB   (1.3%)
  idle VRAM:      7.7 GB   (96%)   ← cash burning at ~$0.40/h on RunPod
```

This is the core motivation for model packing, MPS colocation, and autoscaling.

---

## 2. Batching — What It Actually Means

A GPU is a **SIMD processor** with thousands of cores executing the same instruction on
different data simultaneously. A single sample uses a tiny fraction of those cores:

```
batch=1:    10 000 CUDA cores × [1 sample]   → ~9 900 cores idle
batch=16:   10 000 CUDA cores × [16 samples] → wider matrix, more cores busy
batch=128:  GPU near full utilisation
```

**Wall-clock time grows slowly with batch size.** A forward pass of a 200 MB embedding
model takes roughly the same 4 ms whether batch=1 or batch=16. Throughput therefore
scales nearly linearly:

```
batch=1:   1 sample / 4 ms  =  250 samples/s
batch=8:   8 samples / 5 ms = 1600 samples/s   (6.4× throughput, +1 ms latency)
batch=32: 32 samples / 8 ms = 4000 samples/s   (16× throughput, +4 ms latency)
```

The efficiency curve from Series C/D (eff: N=1→1.00, N=8→0.25) captures this — it takes
25% of single-item time per item in a batch of 8, meaning 4× throughput gain.

**Continuous batching** (vLLM, TGI): for autoregressive LLM generation, requests arrive
at different decoding steps. Continuous batching groups them per token across users,
so the GPU never idles between requests of different lengths. This is what makes
high-concurrency LLM inference economically viable.

### Three batch assembly strategies (established in Series C)

| strategy | description | best for |
|----------|-------------|----------|
| **fixed** | wait until exactly N items collected | predictable load, not latency-sensitive |
| **timeout** | flush after T ms or N items, whichever first | balanced; most common in production |
| **adaptive** | drain whatever is in queue right now | bursty load, lowest latency, exact SimPy match |

---

## 3. Colocation: Multiple Models on One GPU

Three mechanisms, fundamentally different in isolation level and hardware requirements.

### 3a. Naive colocation (weight sharing in VRAM)

Load multiple model weight tensors into GPU memory simultaneously. Switching between
them costs 0 ms — weights are already on-device. The compute cores are still
time-shared, but at the OS scheduler level (no hardware isolation).

```
8 GB GPU with 5 × 200 MB models loaded:
  total weights:  1.0 GB    used for models
  free VRAM:      7.0 GB    available for activations / KV cache
  compute:        shared, sequential within each forward pass
```

Good for: serving a catalogue of small models from one process; resident cache of
hot models avoids reload latency.

### 3b. CUDA MPS (Multi-Process Service)

Multiple CUDA processes (OS processes) share the SM compute units of one GPU
**truly in parallel** — not time-sliced at the kernel level. Each process gets a
fraction of SMs proportional to its thread count.

```
Process A (embedding model): uses 30% of SMs
Process B (classifier):      uses 20% of SMs
Process C (reranker):        uses 25% of SMs
                             ─────────────────
                             25% SMs idle → available for bursts
```

Overhead: shared context management, weaker fault isolation (one process crash can
affect neighbours). Best for: multiple small-model inference servers on the same node
where total load is moderate.

### 3c. MIG — Multi-Instance GPU

**Hardware partitioning**, available on A100 and H100 only. The GPU is divided into
independent slices with dedicated SM groups, memory bandwidth, and VRAM:

```
A100 80 GB MIG topologies (select one):
  7× MIG-10GB   (7 isolated 10 GB "GPUs")
  3× MIG-20GB   (3 isolated 20 GB "GPUs")
  1× MIG-40GB + 2× MIG-20GB
  1× MIG-80GB   (no partition, same as no-MIG)
```

Each slice appears as a separate GPU to the OS. Full memory and compute isolation —
one slice cannot observe or interfere with another. The topology is **fixed at boot**;
dynamic re-partitioning requires bringing the GPU offline.

Best for: multi-tenant SaaS where customers need guaranteed VRAM allocation; running
7 independent model servers on one A100 at ~$3/h instead of 7 × A10G at ~$14/h total.

### 3d. Kubernetes GPU time-slicing

The NVIDIA device plugin allows multiple pods to request the same physical GPU.
Scheduling is time-sliced at the CUDA context level (not hardware-partitioned).
Memory is **not** isolated — all pods share the full VRAM pool and must not exceed it
collectively or they will OOM each other.

Effective for: development clusters, cost reduction when strict isolation is not required.

---

## 4. Heavy Models: Multi-GPU and Tensor Parallelism

When a model does not fit on a single GPU, weights are sharded across GPUs:

**Tensor parallelism:** each attention head or feed-forward layer is split column/row
wise across N GPUs. Each GPU holds 1/N of every layer. Forward pass requires
all-reduce communication between GPUs after each layer.

```
LLaMA-3.1-70B (140 GB fp16) on 4× A100-80GB:
  each GPU holds: 35 GB of weights (every layer, 1/4 of each matrix)
  NVLink bandwidth: 600 GB/s bidirectional
  all-reduce overhead per layer: ~0.5 ms at typical tensor shapes
```

**Pipeline parallelism:** different layers assigned to different GPUs. GPU 0 runs
layers 1–16, GPU 1 runs layers 17–32, etc. Requires micro-batching to keep all GPUs
busy simultaneously; otherwise GPUs idle waiting for activations from the previous stage.

For the simulation, large models are treated as a **single opaque resource** with:
- VRAM requirement: N × single-GPU VRAM
- Allocation unit: a set of GPUs (not individual)
- Inference time: approximately the same as single-GPU (parallelism absorbs the model size)

---

## 5. RunPod as Ephemeral GPU Workers

RunPod (and similar: Lambda Labs, Vast.ai, Modal) provides on-demand GPU instances
with per-minute billing. The key cost driver is **cold start latency** — the time
between requesting an instance and serving the first request:

```
cold start breakdown:
  VM boot:              5–15 s
  Docker pull:         10–60 s  (if not cached)
  model download:       depends on size and storage location
    200 MB from S3:     1–3 s
    16 GB from S3:     30–90 s
    70 GB from S3:    120–300 s
  model load to VRAM:   ~1 s per GB
  ─────────────────────────────
  total for 200 MB model:   ~20–40 s
  total for 16 GB model:    ~90–180 s
  total for 70 GB model:   ~240–480 s
```

**Spot vs on-demand:**
- Spot: ~60–70% cheaper; can be preempted with 30–60 s notice
- On-demand: predictable; SLA-suitable

**Break-even analysis (when to keep an instance warm):**

```
warm instance cost:    $0.40/h = $0.0067/min
cold start amortised:  (cold_start_s / 60) × $0.0067 per cold start

If cold start = 60 s and requests arrive at rate λ:
  cost of cold-starting each request: $0.0067
  break-even: keep warm if idle gap < cold_start_s × (warm_$/s / on_demand_$/s)
```

For 200 MB models (cold start ~30 s): keep warm if inter-request gap < ~4 minutes.
For 16 GB models (cold start ~120 s): keep warm if inter-request gap < ~16 minutes.

---

## 6. Experiment Series E: GPU Cluster Scheduling

Three experiments in ascending complexity, each answering a concrete question.

---

### E-1: Model Packing on a Homogeneous Pool (tiny models)

**Question:** How many 200 MB models with independent Poisson arrival rates can be
efficiently packed onto a fixed pool of 8 GB GPUs? What is the optimal packing
strategy?

**Setup:**

```
GPU pool:   K GPUs, each 8 GB VRAM
Models:     M models, each 200 MB, arrival rate λᵢ ~ Uniform(1, 20) req/s
Inference:  lognormal(μ=log(4ms), σ=0.4) per model, batch assembled with timeout=10ms
Scheduler:  evaluates three packing strategies
```

**Packing strategies to compare:**

| strategy | description |
|----------|-------------|
| **one-model-per-GPU** | baseline; wastes VRAM |
| **first-fit-decreasing** | sort models by load, pack greedily onto GPU with most free VRAM |
| **load-balanced** | assign model to GPU with lowest current queue depth |
| **affinity+rebalance** | sticky assignment, rebalance when queue imbalance > threshold |

**Metrics:**
- GPU VRAM utilisation (% of 8 GB used per GPU)
- GPU compute utilisation (% of time doing forward pass vs idle)
- Per-model P50/P99 latency
- Number of GPUs needed to serve M models at SLA P99 < 50 ms

**Hypothesis:**
- First-fit-decreasing achieves >80% VRAM utilisation vs ~10% for one-model-per-GPU
- Compute utilisation remains the bottleneck at high λ, not VRAM
- Load-balanced beats first-fit-decreasing on tail latency by 20–30% when λ is skewed

---

### E-2: Heterogeneous Scheduler (mixed workload pool)

**Question:** How should a scheduler allocate a mixed queue of embedding (2ms),
reranking (15ms), and small-LLM (200ms) requests across a pool of heterogeneous GPUs?

**Setup:**

```
GPU pool:
  Type A: 4× RTX 4060 Ti  8 GB   → suited for embeddings, rerankers
  Type B: 2× RTX 4090    24 GB   → suited for small LLM (fits quantized 7B)
  Type C: 1× A100        80 GB   → can hold any model; MIG partitionable

Request mix (Poisson, total 50 req/s):
  embedding requests:   60%   λ=30/s   VRAM=200 MB   inference P50=2ms
  reranker requests:    30%   λ=15/s   VRAM=800 MB   inference P50=15ms
  small-LLM requests:   10%   λ=5/s    VRAM=5 GB     inference P50=200ms
```

**Scheduler designs to compare:**

| scheduler | description |
|-----------|-------------|
| **type-affinity** | embeddings → Type A, LLM → Type C, etc. |
| **work-stealing** | any GPU serves any request type; steal from overloaded queues |
| **priority-queues** | LLM=HIGH, reranker=MED, embedding=LOW; preemptible |
| **MIG-aware** | partition A100 into 7× 10 GB; assign model classes to slices |

**Metrics:**
- P99 latency per request class
- GPU idle time per type
- SLA violations (P99 > 2× P50 baseline)
- Total throughput at saturation

**Hypothesis:**
- Type-affinity is efficient under steady load but fails under burst (hot spots)
- Work-stealing improves P99 by 30–40% at the cost of model reload overhead
- MIG-aware scheduler on A100 matches a 3-GPU type-affinity setup at 40% lower cost

---

### E-3: Autoscaling with Ephemeral RunPod Workers

**Question:** Given bursty arrival rates and non-trivial cold-start latency, what
autoscaling policy minimises cost × SLA violations?

**Setup:**

```
Baseline pool:    2 warm GPU instances (always on)
Burst model:      Poisson arrivals, λ normal=10/s, burst=80/s, burst_duration=120s
                  Burst arrives with probability 0.1 per 10-minute window
Cold start:       lognormal(μ=log(30s), σ=0.3) for 200 MB model
Spot preemption:  Poisson(rate=1/h); instance lost, requests in flight fail
Pricing:          warm=$0.40/h, spot=$0.14/h, on-demand=$0.40/h
```

**Autoscaling policies to compare:**

| policy | trigger | scale-down |
|--------|---------|------------|
| **reactive** | queue depth > K → spawn 1 instance | idle > T minutes → terminate |
| **predictive** | EWMA of arrival rate × estimated queue drain time | same |
| **pre-warm pool** | keep N spare cold instances ready (pre-booted, model not loaded) | cost budget |
| **spot+on-demand mix** | use spot for burst, on-demand for baseline | spot preempted → failover |

**Metrics:**
- Cost per 1000 requests ($/1k)
- SLA violations: requests where E2E > 500ms (includes queue wait + inference)
- Cold start penalty: requests delayed by instance startup
- Spot preemption impact: requests lost vs retried

**Hypothesis:**
- Reactive scaling at K=20 queue depth introduces 30s latency spikes at burst start
- Predictive scaling (EWMA window=30s) reduces SLA violations by 60% at +5% cost
- Pre-warm pool of 1 spare instance eliminates 90% of burst cold-start penalty at +$0.40/h
- Spot mix (50% spot, 50% on-demand for baseline) reduces cost by 35% with <2% preemption impact

---

## 7. Simulation Implementation Plan

### E-1 and E-2: SimPy + real asyncio hybrid

Use the same pattern as Series C/D:
- SimPy models the GPU scheduler, queue dynamics, and cost accounting
- Real asyncio models the request handler (same `stages/` pattern) for validation

```python
@dataclass
class GPUInstance:
    gpu_id:        str
    vram_total_mb: int
    vram_used_mb:  int = 0
    models_loaded: dict[str, int] = field(default_factory=dict)  # model_id → vram_mb
    request_queue: simpy.Store = None  # filled at env creation

@dataclass
class ModelSpec:
    model_id:       str
    vram_mb:        int
    mean_ms:        float
    sigma:          float = 0.4
    batch_strategy: str   = "timeout"
    batch_size:     int   = 16
    batch_timeout:  float = 10.0   # ms
```

### E-3: Real multiprocess simulation

Use `ring_blocking_sim.py` architecture: OS processes as GPU worker nodes, `asyncio.Queue`
as request buffer, `time.sleep()` to simulate cold start.

```python
@dataclass
class GPUWorkerSpec:
    worker_id:      str
    vram_gb:        int
    cold_start_s:   float     # drawn from lognormal at spawn time
    spot:           bool
    preemption_rate: float    # events/hour

class AutoscalerPolicy:
    def should_scale_up(self, queue_depth: int, workers: int) -> bool: ...
    def should_scale_down(self, idle_seconds: float, workers: int) -> bool: ...
```

### Cost model

```python
def compute_cost(
    warm_instance_hours: float,
    spot_instance_hours: float,
    cold_starts: int,
    sla_violations: int,
    sla_penalty_per_violation: float = 0.01,  # $0.01 per missed SLA
) -> float:
    return (
        warm_instance_hours * 0.40
        + spot_instance_hours * 0.14
        + sla_violations * sla_penalty_per_violation
    )
```

---

## 8. Key Questions the Experiments Will Answer

| # | question | answered by |
|---|----------|-------------|
| 1 | How many small models fit on one 8 GB GPU without SLA degradation? | E-1 |
| 2 | At what arrival rate does compute, not VRAM, become the bottleneck? | E-1 |
| 3 | Is load-balanced scheduling worth the complexity over first-fit? | E-1 |
| 4 | Does MIG on A100 justify the cost vs a pool of consumer GPUs? | E-2 |
| 5 | Can work-stealing remove the need for type-specific GPU pools? | E-2 |
| 6 | What autoscaling trigger minimises cost without SLA spikes? | E-3 |
| 7 | Is a pre-warm pool cheaper than reactive autoscaling for bursty workloads? | E-3 |
| 8 | What is the real break-even idle time for keeping a GPU warm by model class? | E-3 |

---

## 9. Connection to the Platform Architecture

GPU workers are leaf nodes in the stream_kernel sense. The integration maps cleanly:

```
Celery task (process_fund_load)       →  request arrives at GPU leaf
AsyncRunner inside Celery worker      →  orchestrates model loading + batching
GPUBatcherNode (background asyncio)   →  gpu_stage.run_batcher() pattern from Series C
InMemoryQueue (per GPU worker)        →  request buffer; depth drives autoscaler signal
ObservabilityService                  →  emits GPU utilisation metrics, batch size, cold-start spans
TraceSinkPort → OTelOtlpTraceSink     →  Jaeger/Grafana waterfall per request
```

The autoscaler sits outside the runner — it monitors queue depths across workers
(via Redis counters or a control-plane API) and spawns/terminates worker processes.
This maps directly to the platform's control-plane lifecycle model already built in
`execution/orchestration/lifecycle/`.

For RunPod specifically, "spawning a leaf" becomes an HTTP call to the RunPod API;
"terminating a leaf" is a graceful shutdown via the existing `request_stop()` path on
`AsyncRunner`. The rest of the platform remains unchanged.
