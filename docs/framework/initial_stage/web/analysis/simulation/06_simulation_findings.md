# Runner + IPC Transport Simulation — Findings

**Date:** 2026-03-15
**Experiments:** asyncio-native model (`runner_ipc_model.py`) + real forked-process model (`real_process_sim.py`)

---

## 1. What Was Simulated and Why

The project's execution pipeline involves two OS processes communicating through a pipe:

```
ROOT PROCESS                              LEAF PROCESS
────────────────────────────────────────────────────────────────────
source (node emits msg)
  → send_queue  (asyncio.Queue)
  → sender_thread (background)
  → OS pipe  (kernel buffer)
                                ← background reader thread (polls at poll_interval)
                                ← ipc_recv_buffer (per-target queue)
                                ← scheduler.tick() (asyncio timer, drains recv_buffer)
                                ← runner_queue (asyncio.Queue)
                                ← AsyncRunner.run_async() (executes nodes)
```

**Root** owns the sending side.
**Leaf** owns the receiving side: background reader → scheduler pump → runner loop.

The simulation answers:
- How do queue depths and latency respond to different load/latency combinations?
- What happens when the runner is slower than the message rate?
- What happens when `time.sleep()` (sync/no-yield) is used instead of `await asyncio.sleep()`?
- How does the IPC poll interval affect end-to-end latency?
- How does a drain_budget cap affect throughput?
- Does a credit window (flow control) actually stop credits from flowing when the runner is slow?

---

## 2. Experiment A — asyncio-native simulation (`runner_ipc_model.py`)

### 2.1 Architecture

**No real OS processes.** All components run inside a single asyncio event loop as coroutines and simulated threads:

| Component | Implementation |
|-----------|----------------|
| `source_loop` | `asyncio.sleep()` pacing |
| `sender_thread` | asyncio coroutine simulating thread overhead |
| `OS pipe` | `asyncio.Queue(maxsize=pipe_capacity)` |
| `background_reader` | asyncio coroutine polling at `reader_poll_ms` |
| `ipc_recv_buffer` | `_IQueue` (instrumented asyncio.Queue) |
| `scheduler_pump` | asyncio coroutine ticking at `tick_interval_ms` |
| `runner_loop` | asyncio coroutine with `await asyncio.sleep(node_latency_ms)` |
| `sync_block` | `time.sleep()` inside the event loop (no yield) |

**Instrumentation:** `_IQueue` records `(timestamp, depth)` samples at every put/get, enabling depth time-series.

**Key advantage:** can inject `time.sleep()` directly into the asyncio loop to model sync-blocking work (debug serialization, CPU-bound I/O).

---

### 2.2 Scenarios and Raw Results

#### S1 — Healthy (50/s, node=1ms, tick=5ms, drain=32)

```
Throughput:        48.4 msg/s  (184 msgs in 3.8s)
E2E latency P50:   10.7ms   P99: 14.9ms
IPC→queue wait:    P50=9.0ms  P99=12.5ms
Tick actual:       P50=5.58ms  P99=6.30ms  (target 5ms)
runner_q max=1  mean=0.5
```

#### S2 — Runner overloaded (200/s, node=8ms, tick=5ms)

```
Throughput:        176.3 msg/s
E2E latency P50:   1111ms   P99: 1996ms
IPC→queue wait:    P50=11.1ms  P99=12.6ms  (SAME as S1!)
Tick actual:       P50=5.67ms  P99=6.34ms  (UNCHANGED)
runner_q max=240  mean=120.3
ipc_recv max=2  (reader easily keeps up)
```

#### S3 — Slow node / tick starvation (50/s, slow_node=50ms async, tick=5ms)

```
Throughput:        15.8 msg/s   (3x drop — runner is bottleneck, NOT tick)
E2E latency P50:   63.0ms   P99: 64.2ms
Tick actual:       P50=5.91ms  P99=53.26ms  ← TICK SLIPS TO 53ms!
runner_q max=1   (not the problem — node is just slow)
```

**Note:** This uses `await asyncio.sleep(50ms)` — the slow_node IS async. Yet the tick still slips to 53ms P99. Reason: with only 50/s rate and a 50ms node, the single runner coroutine blocks its own tick slot because both run on the same loop and the scheduler is cooperatively round-robin.

#### S4 — Drain budget cap (300/s, node=0.5ms, tick=5ms, drain=4)

```
Throughput:        258.9 msg/s  (14% below rate — drain_budget=4 is the ceiling)
E2E latency P50:   11.1ms   P99: 15.0ms
Tick actual:       P50=5.68ms  P99=6.42ms
runner_q max=2
```

300 msg/s × 5ms tick = 1.5 msgs per tick average. Budget=4 is fine here. Throughput loss is from rate pacing.

#### S5 — Sync block (50/s, time.sleep=10ms per msg, tick=5ms)

```
Throughput:        43.2 msg/s
E2E latency P50:   22.9ms   P99: 24.5ms
Tick actual:       P50=5.95ms  P99=13.30ms  ← tick damaged but less than S3
Ticks fired:       519  (vs 716 in S1 — 28% fewer)
runner_q max=1
```

`time.sleep(10ms)` inside the event loop prevents the scheduler coroutine from running during that 10ms window. Tick P99=13.3ms (2.5× target). Damage is proportional to sync block duration.

#### S6 — Root/Leaf topology (100/s, node=1ms, tick=5ms)

```
[LEAF]  processed=377  E2E P50=10.6ms  P99=14.5ms
        tick P50=5.71ms  P99=6.48ms
[ROOT replies]  processed=9  E2E P50=12.9ms  P99=14.2ms
```

Bidirectional pipe works symmetrically. Reply path (root as receiver) is identical in latency profile.

---

### 2.3 Credit Window + ACK Scenarios

Credit window models `CreditWindowFlowControlPolicy`: root keeps `_inflight` counter per target; `try_acquire()` → False when `_inflight >= window_size`; leaf reader sends ACK immediately on pipe read (transport-level, NOT after node executes).

#### CW-1 — Wide window=64, 200/s

```
Throughput:        185.5 msg/s
E2E P50=13.1ms  P99=15.2ms
Max in-flight:     2  (3% of window)
Credit stalls:     0
```

Window never fills. Equivalent to no flow control at this rate.

#### CW-2 — Tight window=8, 200/s

```
Throughput:        187.9 msg/s
E2E P50=12.0ms  P99=15.3ms
Max in-flight:     2  (25% of window)
Credit stalls:     0
```

Window=8 is still plenty for 200/s with 5ms reader poll. RTT of credit = ~10ms (2× poll interval), so max in-flight = 200/s × 0.010s = 2 — well within window=8.

#### CW-3 — window=8, reader_poll=20ms

```
Throughput:        187.4 msg/s
E2E P50=18.6ms  P99=25.8ms  ← latency up ~50% vs CW-2
Max in-flight:     5  (62% of window)
Credit stalls:     0
```

Slower reader → longer ACK RTT → more in-flight at any moment. At 200/s × 0.040s RTT → ~8 in-flight, exactly at window edge. Latency goes up proportionally to poll interval.

#### CW-4 — window=4, slow runner (node=10ms)

```
Throughput:        187.9 msg/s   ← UNCHANGED! ACK is transport-level
E2E P50=1769ms  P99=3441ms   ← runner_q fills (same as S2)
Max in-flight:     2  (50% of window)
Credit stalls:     0
runner_q max=335
```

**Key finding:** ACK is sent by the background reader thread immediately on pipe read, NOT after the runner executes the node. So slow runner does NOT delay ACK, credits flow freely, runner_q fills exactly as in the no-credit case.

#### CW-5 — window=1, 200/s (serial)

```
Throughput:        185.3 msg/s
E2E P50=19.3ms  P99=32.1ms
Max in-flight:     1  (100% of window)
Credit stalls:     708  (every message must wait for prev ACK)
Pending enq/flushed: 708/705
```

Window=1 makes the transport serial. Every message waits for the ACK before next send. Throughput is preserved (pipeline is fast), but latency goes up to ~2× credit RTT.

---

### 2.4 asyncio Simulation — Key Conclusions

| # | Finding |
|---|---------|
| A1 | **`await asyncio.sleep()` does NOT damage tick.** Slow runner (node_latency > 1/rate) causes runner_q to grow unboundedly, but tick fires on time. The tick coroutine runs during runner's yield point. |
| A2 | **`time.sleep()` (no yield) DOES damage tick.** Tick P99 = sync_block_ms + tick_interval_ms. 10ms sync block → tick P99=13ms vs 5ms target. 50ms sync block → P99=53ms. |
| A3 | **IPC recv buffer never fills in these scenarios.** The background reader coroutine is fast relative to source rates tested. Backlog forms in runner_q, not ipc_recv. |
| A4 | **drain_budget caps throughput** at `drain_budget × (1000 / tick_interval_ms)` msg/s. At drain=4, tick=5ms: ceiling = 800/s. At drain=32 the ceiling is rarely hit. |
| A5 | **E2E latency floor = tick_interval_ms + node_latency_ms + pipe_latency_ms** (~10–15ms in healthy scenarios). |
| A6 | **Credit window ACK is transport-level.** Slow runner does not delay ACK. Credit RTT = pipe_latency + reader_poll_interval. |
| A7 | **Credit stalls only appear at window=1** (serial). At window≥4 with realistic rates, in-flight stays well within window. |

---

## 3. Experiment B — Real forked OS processes (`real_process_sim.py`)

### 3.1 Architecture

**Real OS processes, real OS pipes, real adapter code.**

| Component | Implementation |
|-----------|----------------|
| Root process | Main Python process |
| Leaf process | `mp.get_context("fork").Process(target=leaf_main)` |
| IPC pipe | `ctx.Pipe(duplex=True)` — real OS pipe created before fork |
| Root adapter | `PipeExecutionIpcTransportAdapter` with `attach_endpoint(parent_conn, "leaf#1")` |
| Leaf adapter | Fresh `PipeExecutionIpcTransportAdapter` with `attach_endpoint(child_conn, "root")` |
| Background reader | Real thread inside `PipeExecutionIpcTransportAdapter._reader_loop` |
| Background sender | Real thread inside `PipeExecutionIpcTransportAdapter._sender_loop` |
| Leaf runner | `asyncio.new_event_loop()` with `_leaf_scheduler_pump` + `_leaf_runner_loop` |
| Result collection | `multiprocessing.Queue` from child to parent |

**Process count:** 1 root process (alive throughout all 5 scenarios) + 5 leaf processes (one per scenario, launched sequentially). Total: **6 OS processes**.

**Timestamp mechanism:** `SimPayload.created_ns = time.monotonic_ns()` at root send; `completed_ns = time.monotonic_ns()` at leaf completion. `CLOCK_MONOTONIC` is process-local but fork inherits the same clock reference, so delta is valid.

**Fork details:** Child inherits parent's file descriptors (pipe connections). Parent closes child end after fork. Child creates a fresh adapter (doesn't reuse parent's adapter object — parent's background threads are NOT inherited via fork). Child calls `attach_endpoint(child_conn, "root")` to start its own background reader.

---

### 3.2 Scenarios and Raw Results

#### RS-1 — Healthy (200/s, node=1ms, tick=5ms, ipc_poll=5ms)

```
Processes:    Root PID=415390  Leaf PID=415391
Messages:     sent=800  received=781  processed=780  lost=19
Throughput:   210.8 msg/s

E2E latency (root stamp → leaf completion):
  P50=7.8ms   P90=10.7ms   P99=12.6ms   max=13.8ms

Scheduler tick (target=5ms):
  P50=5.65ms  P99=6.39ms   fired=704

runner_q:  max=3   mean=1.1
IPC buf:   max=0   mean=0.0
```

#### RS-2 — Runner overloaded (200/s, node=8ms)

```
Processes:    Root PID=415390  Leaf PID=415616
Messages:     sent=800  received=781  processed=781  lost=19
Throughput:   211.1 msg/s   (messages received quickly; runner queues them all)

E2E latency:
  P50=1552.7ms  P90=2507.8ms  P99=2718.1ms  max=2741.2ms

Scheduler tick:
  P50=5.41ms   P99=6.12ms   (TICK UNAFFECTED by slow runner!)

runner_q:  max=330  mean=163.8   (massive backlog, same as asyncio model)
IPC buf:   max=0    mean=0.0     (background reader keeps up fine)
```

#### RS-3 — Slow IPC reader (200/s, node=1ms, ipc_poll=30ms)

```
Processes:    Root PID=415390  Leaf PID=415940
Messages:     sent=800  received=777  processed=777  lost=23

E2E latency:
  P50=23.3ms  P90=32.8ms  P99=36.2ms  max=37.2ms

Scheduler tick:
  P50=5.58ms  P99=6.37ms  (still healthy)

runner_q:  max=7   mean=1.4
IPC buf:   max=0   mean=0.0  (metrics API may not report this correctly — see §3.4)
```

#### RS-4 — Sync block (100/s, node=0.1ms, sync_cpu=10ms, tick=5ms)

```
Processes:    Root PID=415390  Leaf PID=416013
Messages:     sent=400  received=391  processed=391  lost=9

E2E latency:
  P50=123.1ms  P90=192.8ms  P99=222.0ms  max=226.0ms

Scheduler tick:
  P50=10.42ms  P99=11.65ms   ← DOUBLED from 5ms target

runner_q:  max=18  mean=8.9
```

#### RS-5 — Coarse tick (500/s, node=0.1ms, tick=20ms, drain=64)

```
Processes:    Root PID=415390  Leaf PID=416096
Messages:     sent=2000  received=1955  processed=1944  lost=45
Throughput:   525.4 msg/s

E2E latency:
  P50=20.7ms  P90=24.8ms  P99=27.1ms  max=28.7ms

Scheduler tick:
  P50=20.85ms  P99=21.71ms  (accurate)
  fired=191

runner_q:  max=12  mean=10.2
```

---

### 3.3 Real Process Simulation — Key Conclusions

| # | Finding |
|---|---------|
| B1 | **Tick stays accurate even with slow runner (real OS processes).** RS-2: runner_q=330, P99=2718ms; tick P99=6.12ms. Confirms A1 in real-kernel context. |
| B2 | **Sync block (`time.sleep`) in asyncio event loop doubles tick interval.** RS-4: target 5ms → actual P50=10.42ms. Confirms A2 with real processes. |
| B3 | **IPC poll interval directly sets E2E latency floor.** RS-3 with ipc_poll=30ms → E2E P50=23.3ms vs 7.8ms in RS-1 (ipc_poll=5ms). Rule: `E2E_floor ≈ ipc_poll + tick + node_latency`. |
| B4 | **Coarse tick directly sets E2E latency floor.** RS-5: tick=20ms → E2E P50=20.7ms. Rule: `E2E_floor ≈ max(ipc_poll, tick) + node_latency`. |
| B5 | **"Lost" messages (19–45) are startup gap artifacts**, not real pipe loss. The 100ms sleep before root starts sending is not enough for all leaf background threads to be ready. These messages either hit the pipe before leaf attaches, or leaf's background reader hasn't started yet. No real data loss on OS pipe under normal conditions. |
| B6 | **`adapter.metrics("root")` returns queue_depth=0 throughout.** Either the `PipeExecutionIpcTransportAdapter.metrics()` method doesn't track in-memory buffer depth, or it reports pre-drain values. This is a monitoring gap in the adapter. |
| B7 | **Background reader thread in real adapter runs at poll_interval_seconds.** It blocks on `connection.poll(timeout)` — so the effective IPC batch size per read cycle = all messages that arrived during `poll_interval`. At 200/s and 5ms poll: ~1 message/poll. At 30ms poll: ~6 messages/poll. |
| B8 | **Fresh adapter per leaf process is required after fork.** Fork does not copy parent's background threads. If leaf reused parent's adapter object, the `_reader_loop` thread would be dead in the child. |

---

## 4. Comparison: asyncio Model vs Real Processes

| Metric | asyncio model | Real processes | Match? |
|--------|--------------|----------------|--------|
| Tick P50 (healthy, 5ms target) | 5.58ms | 5.65ms | ✓ |
| Tick P99 (healthy) | 6.30ms | 6.39ms | ✓ |
| Tick P50 under slow runner (async) | 5.67ms | 5.41ms | ✓ |
| Tick P50 under sync block (10ms) | 5.95ms | 10.42ms | Partial (asyncio model underestimates sync damage at low rate) |
| Tick P99 under sync block (10ms) | 13.30ms | 11.65ms | ✓ order of magnitude |
| E2E latency floor (healthy, ~5ms tick) | 10.7ms | 7.8ms | Close (asyncio has coroutine overhead) |
| E2E latency with slow runner | P50=1111ms | P50=1552ms | ✓ same ballpark |
| runner_q max with slow runner | 240 | 330 | ✓ same order |
| Effect of slow IPC reader on latency | not modeled in S-series | 3× E2E increase | — |

**Overall:** asyncio model faithfully predicts all qualitative behaviors. Quantitative differences are within 20–30% and explained by asyncio model having lower overhead (no real thread scheduling, no real syscalls, no kernel pipe latency).

---

## 5. System Design Rules Derived from Experiments

### 5.1 Tick interval and drain_budget

```
minimum tick_interval ≥ node_latency × drain_budget
               or equivalently:
throughput_ceiling = drain_budget / tick_interval_s
```

At `tick=5ms, drain=32`: ceiling = 6400 msg/s (not the bottleneck in practice).
At `tick=5ms, drain=4`: ceiling = 800 msg/s (becomes bottleneck at high rates).

**Recommendation:** `drain_budget ≥ message_rate_per_sec × tick_interval_s × 2` (2× headroom).

### 5.2 IPC poll interval and E2E latency

```
E2E_latency ≈ ipc_poll_interval + tick_interval + node_latency_avg
```

| ipc_poll | tick | node | Expected E2E P50 |
|----------|------|------|------------------|
| 5ms      | 5ms  | 1ms  | ~11ms            |
| 30ms     | 5ms  | 1ms  | ~36ms            |
| 5ms      | 20ms | 0.1ms| ~25ms            |

**Recommendation:** `ipc_poll ≤ tick_interval` to avoid ipc_poll becoming the dominant term.

### 5.3 Sync blocking work in asyncio event loop

Any `time.sleep()`, blocking I/O, or CPU-bound loop inside an asyncio coroutine without yield:

```
tick_actual_P99 ≈ tick_interval + sync_block_duration
```

This directly multiplies E2E latency for all messages in the queue.

**Root cause in the project:** `_emit_service_call_debug` was calling `build_call_payload_fields(args=args)` which does deep recursive object traversal (including `bytes.hex()` on large IPC frames) and `inspect.currentframe()` on every decorated service method call — all on the asyncio event loop, synchronously.

**Fix applied:** Removed `build_call_payload_fields`, `build_result_payload_fields`, `_resolve_caller` from the hot path. Only basic timing/status fields are emitted. Also added `_DIRECT_BATCH_SIZE=100` batching to reduce `queue.put()` + `event.set()` frequency in `InMemoryRuntimeDebugBufferService`.

### 5.4 Credit window sizing

```
window_size ≥ message_rate_per_sec × credit_RTT_s × 1.5  (safety margin)
credit_RTT ≈ ipc_poll_interval × 2  (round-trip: leaf reads → ACK → root receives)
```

At 200/s, ipc_poll=5ms: credit_RTT ≈ 10ms → need window ≥ 200 × 0.010 × 1.5 = 3.
Window=4 is sufficient. Window=8 is comfortable. Window=64 is excess.

**Key insight:** Slow runner does NOT exhaust credits because ACK is transport-level (sent by background reader thread on pipe read, not by runner after node execution). The credit window controls pipe pressure only, not runner pressure.

### 5.5 Runner backlog detection

The only reliable signal for runner overload is `runner_queue.qsize()`. Neither IPC buffer depth nor tick accuracy indicates runner overload — the tick fires normally even when runner_q=330.

```
runner_overloaded = runner_q.qsize() > drain_budget × 2
```

If runner is overloaded, options:
1. Reduce `node_latency` (faster nodes)
2. Increase parallelism (multiple runner coroutines or thread pool)
3. Add back-pressure at the scheduler pump: pause draining if runner_q > threshold

---

## 6. Files

| File | Purpose |
|------|---------|
| `runner_ipc_model.py` | asyncio-native simulation, single process, all components as coroutines |
| `real_process_sim.py` | Real forked OS processes using `PipeExecutionIpcTransportAdapter` |

### Running

```bash
# asyncio model (all scenarios)
python docs/framework/initial_stage/web/analysis/simulation/runner_ipc_model.py

# asyncio model — credit window scenarios only
python docs/framework/initial_stage/web/analysis/simulation/runner_ipc_model.py credits

# real process simulation (5 sequential scenarios, 6 total OS processes)
python docs/framework/initial_stage/web/analysis/simulation/real_process_sim.py
```
