# Simulation model design

## Problem

We want to run controlled experiments on the stream_kernel + fund_load system **without**
starting real processes: model IPC throughput, scheduler drain fairness, backpressure,
and end-to-end latency under various load profiles.

## Library choice: SimPy

**SimPy** (discrete-event simulation) fits best:

- Generator-based Python processes — each simulated actor is a plain generator that `yield`s
  events; maps directly to runner loops and scheduler ticks.
- `simpy.Store` — bounded FIFO queue; natural model for IPC lane buffers and runner queues.
- `simpy.Container` — resource pool; natural model for credit-based flow control windows.
- Deterministic replay from a fixed random seed — reproducible experiments.
- No extra infrastructure; pure Python.

Install: `pip install simpy`.

## Core principle: discovery → topology → simulation

`build_topology_snapshot(config_path)` already does all structural analysis using
stream_kernel's own discovery mechanisms:

```
YAML config
    │
    ▼
build_topology_snapshot()          ← uses stream_kernel discovery
    │  validate_newgen_config
    │  load_discovery_modules
    │  discover_nodes / discover_services / discover_adapters
    │
    ▼
topology: {
  "processes": [...],  ← one entry per OS process (root + N leaf workers)
  "pipes":     [...],  ← one entry per root↔leaf IPC connection
  "catalog":   {...},  ← all discovered nodes/services/adapters
}
    │
    ▼
build_simulation(topology, experiment_cfg)
    │  one ProcessSim per process
    │  one PipeSim per pipe (5 lanes each)
    │  one SchedulerPumpSim per process
    │  one SourceSim + SinkSim
    │
    ▼
simpy.Environment().run(until=T)
    │
    ▼
SimulationResult: latency series, queue-depth series, throughput, drain stats
```

The simulation is **config-driven in the same way the real system is**: change the YAML,
rebuild the topology snapshot, re-run the simulation.  No separate topology description
is needed.

---

## Simulation entity model

### 1. ProcessSim

One SimPy process per entry in `topology["processes"]`.

Internal state:
- `runner_queue: simpy.Store(env, capacity=runner_queue_capacity)`
- Per-lane `recv_buffer: dict[lane, simpy.Store]` (populated by SchedulerPumpSim)
- Sequential node chain derived from `process["nodes"]` (order preserved from config)

Runner loop (generator):

```python
def runner_loop(env, proc):
    while True:
        msg = yield proc.runner_queue.get()
        for node_name in proc.business_node_names:
            latency = proc.node_latency(node_name)
            yield env.timeout(latency)
        proc.outbound_dispatch(msg)
```

`outbound_dispatch` puts the processed message to the `PipeSim` data lane for the next
process group (or to `SinkSim` if this is the final egress group).

### 2. PipeSim

One `PipeSim` per entry in `topology["pipes"]`, representing the bidirectional IPC channel
between root and a leaf worker.

Per lane (`control`, `data`, `trace`, `log`, `metric`):
- `send_store: simpy.Store(env, capacity=lane_capacity)`   ← sender puts here
- Drained by the SchedulerPumpSim of the receiving process

Flow control (data lane only by default):
- `credits: simpy.Container(env, capacity=window_size, init=window_size)`
- Sender does `yield pipe.credits.get(1)` before `pipe.send_store.put(msg)`
- Receiver releases credits back after message is consumed downstream

```python
@dataclass
class PipeSim:
    pipe_id: str
    from_process: str
    to_process: str
    lanes: dict[str, simpy.Store]          # lane_name → store
    credits: simpy.Container               # flow-control window
    transport_latency_ms: float = 0.05     # one-way wire delay (configurable)
```

### 3. SchedulerPumpSim

One per process. Runs on every scheduler tick (`scheduler_tick_interval_ms`).

Implements weighted round-robin with starvation guard:

```
lane weights (default): control=4, data=8, trace=2, log=1, metric=1
per-target burst cap:   16
global per-tick budget: 256
```

Algorithm per tick:

```python
def scheduler_pump(env, proc, pipes_in, tick_interval, weights, budget, burst_cap):
    while True:
        remaining = budget
        for lane in DRAIN_ORDER:  # stable order: control, data, trace, log, metric
            allotment = min(weights[lane], remaining)
            drained = 0
            for pipe in pipes_in:
                store = pipe.lanes[lane]
                while drained < allotment and drained < burst_cap and store.items:
                    msg = store.items.pop(0)  # direct pop (no yield needed in sim)
                    proc.runner_queue.put(msg)
                    if lane == "data":
                        pipe.credits.put(1)
                    drained += 1
            remaining -= drained
            if remaining <= 0:
                break
        yield env.timeout(tick_interval)
```

The deterministic lane order and per-target cap match the reference policy in
`IPC transport buffer-drain and scheduler pump model.md`.

### 4. SourceSim

Models the pull-file source attached to the ingress leaf process.

```python
def source_loop(env, ingress_proc, message_count, batch_size, inter_batch_ms):
    for i in range(message_count):
        msg = Message(id=i, created_at=env.now)
        yield ingress_proc.runner_queue.put(msg)
        if (i + 1) % batch_size == 0:
            yield env.timeout(inter_batch_ms)
```

Pacing modes:
- `batch`: emit `batch_size` messages, then wait for `sink_dispatch_ack` signal (modeled
  as a `simpy.Event` released when the egress process dispatches the corresponding batch).
- `all`: emit all messages back-to-back (no ack-gating).

### 5. SinkSim

Consumes messages arriving at the egress process, records end-to-end latency.

```python
def sink_loop(env, egress_proc, results):
    while True:
        msg = yield egress_proc.sink_queue.get()
        results.record(latency=env.now - msg.created_at, msg_id=msg.id)
```

---

## Experiment configuration

```python
@dataclass
class SimExperimentConfig:
    # Load
    message_count: int = 1000
    source_batch_size: int = 1
    inter_batch_ms: float = 0.0
    pacing_mode: str = "batch"           # "batch" | "all"

    # Node latency model (per node name, fallback to default)
    node_latencies: dict[str, LatencyConfig] = field(default_factory=dict)
    default_node_latency: LatencyConfig = field(
        default_factory=lambda: LatencyConfig(kind="deterministic", mean_ms=0.1)
    )

    # Scheduler
    scheduler_tick_interval_ms: float = 1.0
    lane_weights: dict[str, int] = field(default_factory=lambda: {
        "control": 4, "data": 8, "trace": 2, "log": 1, "metric": 1
    })
    drain_budget: int = 256
    burst_cap: int = 16

    # IPC
    credit_window_size: int = 4096
    lane_capacity: int = 131072         # matches queue_max_items in config
    transport_latency_ms: float = 0.05  # pipe wire delay

    # Runner
    runner_queue_capacity: int = 4096

    # Reproducibility
    random_seed: int = 42

@dataclass
class LatencyConfig:
    kind: str = "deterministic"   # "deterministic" | "exponential" | "normal"
    mean_ms: float = 0.1
    std_ms: float = 0.0           # for "normal"
```

---

## Entry point

```python
def run_simulation(
    config_path: str | Path,
    experiment: SimExperimentConfig,
    sim_time_limit_ms: float = 60_000.0,
) -> SimulationResult:
    topology = build_topology_snapshot(str(config_path))
    env = simpy.Environment()
    sim = build_simulation(env, topology, experiment)
    env.run(until=sim_time_limit_ms)
    return sim.collect_results()
```

`build_simulation(env, topology, experiment)`:

1. For each entry in `topology["processes"]` → instantiate `ProcessSim`.
2. For each entry in `topology["pipes"]` → instantiate `PipeSim` with 5 lane stores +
   credit container; wire `from_process.outbound → pipe.send_store` and
   `pipe.drain → to_process.recv_buffer`.
3. For each `ProcessSim` → attach `SchedulerPumpSim` (reads from inbound `PipeSim` lanes).
4. Find process with `group_kind == "business"` and ingress role → attach `SourceSim`.
5. Find process with egress role → attach `SinkSim`.
6. Start all SimPy processes: `env.process(proc.runner_loop())` for each actor.

The mapping from `topology["pipes"]` is exact: `pipe["from_process"]` and
`pipe["to_process"]` are the same IDs used in `topology["processes"]`, so wiring is
mechanical (no hand-written adjacency list).

---

## Metrics collected

```python
@dataclass
class SimulationResult:
    # Latency
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float

    # Throughput
    throughput_msgs_per_ms: float
    total_duration_ms: float

    # Queue depth snapshots (sampled every tick)
    queue_depth_series: dict[str, list[tuple[float, int]]]  # entity_id → [(t, depth)]

    # Drain stats (per tick)
    drain_budget_consumed_series: list[tuple[float, int]]   # [(t, consumed)]
    drain_per_lane_series: dict[str, list[tuple[float, int]]]

    # Credit utilization
    credit_utilization_series: dict[str, list[tuple[float, float]]]  # pipe_id → [(t, ratio)]
```

---

## Integration with research_ui

New module: `research_ui/simulation/`.

```
research_ui/simulation/
    __init__.py
    builder.py        ← build_simulation()
    entities.py       ← ProcessSim, PipeSim, SchedulerPumpSim, SourceSim, SinkSim
    config.py         ← SimExperimentConfig, LatencyConfig
    metrics.py        ← SimulationResult, MetricsCollector
    runner.py         ← run_simulation()
```

FastAPI router `research_ui/api/simulation.py`:

```
POST /simulation/run
  body: { config_path, experiment: SimExperimentConfig }
  → SimulationResult (JSON)

GET /simulation/presets
  → list of named experiment presets (baseline vs high-load vs observability-heavy)
```

Frontend page (`research_ui/webapp.py`):
- Renders latency and throughput curves using the existing chart components.
- Optionally overlays real Jaeger trace summary for comparison.

---

## What the simulation can answer

| Question | How modeled |
|---|---|
| At what message rate does the data lane saturate? | SourceSim rate vs credit container |
| Does the control lane starve under heavy data load? | SchedulerPumpSim weights + starvation guard |
| What is the theoretical end-to-end P99 with N process groups? | node latency chain + pump drain delay |
| Is a larger credit window worth the memory cost? | vary `credit_window_size` in experiment |
| What drain budget is needed to keep queue depth < X? | vary `drain_budget`, observe queue_depth_series |
| Does observability-lane load affect business latency? | add synthetic trace/log messages from each proc |

---

## Implementation sequence (TDD)

1. `SimExperimentConfig` schema + serialisation tests.
2. `PipeSim` unit: put N messages → pump drains → correct count in runner queue.
3. `SchedulerPumpSim` fairness tests:
   - control lane serviced every tick under sustained data load;
   - data lane higher throughput share;
   - trace/log/metric never zero after K ticks.
4. Single-process roundtrip: source → node chain → sink, latency recorded.
5. Two-process boundary: ingress leaf → root → egress leaf.
6. Full topology from YAML config: run 100 messages, assert all arrive at sink.
7. FastAPI endpoint: POST request returns JSON result.
