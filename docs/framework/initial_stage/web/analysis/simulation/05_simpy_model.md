# SimPy model: faithful representation of all four layers

## Design principle

The simulation must faithfully represent the **actual execution model** (Layer 1), not just
the abstract pipeline topology. This means:

- IPC lanes as separate SimPy stores (not a single queue).
- Background reader loop as a separate SimPy process (not implicit).
- Scheduler tick as a timer-driven SimPy process (not event-driven).
- Credit window as a `simpy.Container` with explicit acquire/release.
- Pending outbound backlog as a Python list drainable on credit release.

The structural configuration (which nodes, which process groups, which pipes) comes from
`build_topology_snapshot()` — so the sim stays in sync with the YAML config.

## Core SimPy entities

### ProcessSim

```python
@dataclass
class ProcessSim:
    process_id: str           # "root", "execution.ingress#1", etc.
    group_kind: str           # "root" | "business" | "observability"
    runner_queue: simpy.Store
    lane_recv_buffers: dict[str, simpy.Store]  # lane → buffer
    pending_outbound: list[PendingMessage]      # credit-pending outbound

    # Derived from topology node list
    node_chain: list[NodeSim]

    def runner_loop(self, env):
        while True:
            msg = yield self.runner_queue.get()
            yield env.process(self._execute_chain(msg))

    def _execute_chain(self, env, msg):
        for node in self.node_chain:
            yield env.timeout(node.sample_latency())
            # If node produces boundary output: call _dispatch_to_pipe(msg)
```

### PipeSim

Five lanes per root↔leaf connection:

```python
@dataclass
class PipeSim:
    pipe_id: str
    from_process: str
    to_process: str
    # Five per-lane in-flight queues (models OS pipe + serialization)
    lanes: dict[str, simpy.Store]     # capacity = lane_capacity
    # Credit container (models CreditWindowFlowControlPolicy)
    credits: simpy.Container           # init=window_size
    # Pending outbound (models _pending in coordinator)
    pending: deque[PendingMessage]

    def try_send(self, env, lane, msg, credits_required=1):
        """Non-blocking: enqueue to pending if no credits."""
        if lane in {"control", "data"}:
            if self.credits.level >= credits_required:
                yield self.credits.get(credits_required)
                yield self.lanes[lane].put(msg)
            else:
                self.pending.append(PendingMessage(msg, lane, credits_required))
        else:
            # Observability lanes: no credit gating
            if self.lanes[lane].items.__len__() < self.lanes[lane].capacity:
                yield self.lanes[lane].put(msg)
            # else: drop (non_block policy)

    def release_credits(self, env, count):
        """Called by reader sim when ACK received. Flushes pending."""
        yield self.credits.put(count)
        yield env.process(self._flush_pending(env))

    def _flush_pending(self, env):
        while self.pending and self.credits.level >= self.pending[0].credits_required:
            entry = self.pending.popleft()
            yield self.credits.get(entry.credits_required)
            yield self.lanes[entry.lane].put(entry.msg)
```

### ReaderLoopSim

Models the background reader thread. Drains lane stores into ProcessSim recv_buffers.
Sends ACK control signals back to sender's credit container.

```python
def reader_loop(env, pipe, to_proc, poll_interval_ms=5.0):
    """Simulates PipeExecutionIpcTransportAdapter background reader thread."""
    while True:
        for lane in ALL_LANES:
            while pipe.lanes[lane].items:
                msg = yield pipe.lanes[lane].get()
                yield to_proc.lane_recv_buffers[lane].put(msg)
                # If lane is data: send ACK back to sender's credit container
                if lane in {"control", "data"}:
                    yield env.process(pipe.release_credits(env, 1))
        yield env.timeout(poll_interval_ms)
```

### SchedulerPumpSim

Models `system.scheduler.tick`: drains recv_buffers into runner_queue.

```python
def scheduler_pump(env, proc, tick_interval_ms, weights, drain_budget, burst_cap):
    while True:
        remaining = drain_budget
        for lane in DRAIN_ORDER:           # control, data, trace, log, metric
            allotment = min(weights[lane], remaining)
            drained = 0
            buf = proc.lane_recv_buffers[lane]
            while drained < allotment and drained < burst_cap and buf.items:
                msg = yield buf.get()
                yield proc.runner_queue.put(msg)
                drained += 1
            remaining -= drained
            if remaining <= 0:
                break
        yield env.timeout(tick_interval_ms)
```

**Key fidelity point:** the scheduler only fires after waiting `tick_interval_ms`. Under
heavy load in the real system, ticks are delayed because the runner loop is busy. To model
this, we can track runner occupancy and delay tick proportionally.

### SenderThreadSim

Models `_PipeSendBuffer` + background sender thread. Adds OS pipe latency.

```python
def sender_thread(env, pipe, lane, os_pipe_capacity, send_latency_ms):
    """Simulates background sender thread writing to OS pipe."""
    # The pipe.lanes[lane] store IS the OS pipe buffer (bounded by os_pipe_capacity).
    # Each put() models one os.write() call with associated latency.
    # If the store is full, the put() blocks — simulating OS pipe buffer saturation.
    while True:
        msg = yield pipe.send_queue[lane].get()    # drain from sender buffer
        yield env.timeout(send_latency_ms)         # serialization + os.write
        yield pipe.lanes[lane].put(msg)            # may block if OS pipe full
```

## Instrumentation hooks

All stores and containers emit measurements at each `put/get` with simpy callbacks:

```python
class InstrumentedStore(simpy.Store):
    def __init__(self, env, capacity, name):
        super().__init__(env, capacity)
        self.name = name
        self._depth_series: list[tuple[float, int]] = []

    def put(self, item):
        event = super().put(item)
        self._depth_series.append((self._env.now, len(self.items)))
        return event

    def get(self):
        event = super().get()
        self._depth_series.append((self._env.now, len(self.items)))
        return event
```

The `_depth_series` becomes the queue-depth time-series in `SimulationResult`.

## Node latency model

Three modes:

```python
@dataclass
class LatencyConfig:
    kind: str = "deterministic"   # deterministic | exponential | normal
    mean_ms: float = 0.1
    std_ms: float = 0.0

def sample_latency(cfg: LatencyConfig, rng: random.Random) -> float:
    if cfg.kind == "deterministic":
        return cfg.mean_ms
    if cfg.kind == "exponential":
        return rng.expovariate(1.0 / cfg.mean_ms)
    if cfg.kind == "normal":
        return max(0.0, rng.gauss(cfg.mean_ms, cfg.std_ms))
    return cfg.mean_ms
```

For H1 (OTLP blocking), use `exponential` with `mean_ms=50` on a trace-emit node.
This represents the real distribution of HTTP POST latencies.

## Building the simulation from topology

```python
def build_simulation(
    env: simpy.Environment,
    topology: dict,
    experiment: SimExperimentConfig,
) -> SimulationHandle:
    procs: dict[str, ProcessSim] = {}
    pipes: dict[str, PipeSim] = {}

    # 1. Create ProcessSim for each process in topology
    for proc_desc in topology["processes"]:
        proc = ProcessSim.from_descriptor(proc_desc, env, experiment)
        procs[proc.process_id] = proc

    # 2. Create PipeSim for each pipe in topology
    for pipe_desc in topology["pipes"]:
        pipe = PipeSim.from_descriptor(pipe_desc, env, experiment)
        pipes[pipe.pipe_id] = pipe

    # 3. Wire: ProcessSim.outbound_dispatch → PipeSim.try_send
    for pipe in pipes.values():
        from_proc = procs[pipe.from_process]
        to_proc = procs[pipe.to_process]
        from_proc.set_outbound_pipe(pipe)

    # 4. Start reader loops
    for pipe in pipes.values():
        to_proc = procs[pipe.to_process]
        env.process(reader_loop(env, pipe, to_proc, experiment.poll_interval_ms))

    # 5. Start scheduler pumps
    for proc in procs.values():
        env.process(scheduler_pump(
            env, proc,
            tick_interval_ms=experiment.scheduler_tick_interval_ms,
            weights=experiment.lane_weights,
            drain_budget=experiment.drain_budget,
            burst_cap=experiment.burst_cap,
        ))

    # 6. Attach source and sink
    ingress_proc = _find_proc(procs, group_name="execution.ingress")
    egress_proc = _find_proc(procs, group_name="execution.egress")
    source = SourceSim(env, ingress_proc, experiment)
    sink = SinkSim(env, egress_proc)
    env.process(source.source_loop())
    env.process(sink.sink_loop())

    return SimulationHandle(procs, pipes, source, sink)
```

## Scheduler tick fidelity improvement

To model the H1 scenario (tick starvation when runner is busy), add runner occupancy tracking:

```python
def scheduler_pump_with_occupancy(env, proc, base_tick_interval_ms, ...):
    while True:
        # If runner was busy last cycle, extend tick interval
        occupancy_ratio = proc.runner_busy_time_ms / base_tick_interval_ms
        effective_interval = base_tick_interval_ms * (1.0 + occupancy_ratio)
        remaining = drain_budget
        # ... drain loop ...
        yield env.timeout(effective_interval)
```

This models the cooperative-multitasking degradation of asyncio when a coroutine holds
the event loop for a long time.

## Expected outputs for H1 validation

If OTLP latency = 50ms and scheduler_tick = 1ms:

- Effective tick cadence: should degrade to ~50ms (occupied by trace emit).
- Data lane pipe queue depth: should grow at ~(message_rate × 50ms) depth.
- End-to-end P99 latency: should be much larger than node processing time alone.
- Credit utilization: should approach window ceiling → `_pending` backlog grows.

If OTLP latency = 0ms (trace queue activated):

- Effective tick cadence: stays at 1ms.
- Queue depths: near-zero.
- E2E P99: bounded by node processing only.

This directly answers the question: **is the trace_queue dead code the primary stall cause?**
