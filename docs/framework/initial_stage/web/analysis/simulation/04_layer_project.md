# Layer 4 — Project: fund_load stall investigation

## Observed symptom

System works but catches a stall. Cannot identify the jam point through thought experiments.

## Configuration in play (from baseline_config_newgen_multiprocess_jaeger.yml)

```yaml
flow_control:
  mode: credits
  credits:
    window_size: 4096          # divided by group weights → ~682–1024 per group

boundary_dispatch:
  mode: stream
  control_poll_ms: 1.0
  batch_max_items: 1
  stream_batch_max_items: 1
  timeout_seconds: 600.0
  inflight_idle_timeout_seconds: 5.0

runner_loop:
  reply_drain_max_items: 256
  post_start_settle_enabled: true
  post_start_settle_quiet_window_seconds: 1.0

process_groups:
  execution.ingress:   workers=1, nodes=3 (source, ingress_line_bridge, parse_load_attempt)
  execution.features:  workers=1, nodes=3 (compute_time_keys, idempotency_gate, compute_features)
  execution.policy:    workers=1, nodes=2 (evaluate_policies, update_windows)
  execution.egress:    workers=1, nodes=3 (format_output, egress_line_bridge, sink)
```

## Process topology correction

**OTLP lives in a dedicated `observability_worker` OS process — NOT in root.**

Evidence from code:
- `builder.py`: when `bootstrap_mode == "process_supervisor"` and `service_process_enabled`
  and `role != "observability_worker"` → root returns early without building OTLP adapters.
- `lifecycle_manager.py`: `_partition_shutdown_workers()` separates `observability_workers`
  and stops them after regular workers.
- `leaf/plan_builder.py`: `observability_worker` role gets `trace`, `log`, `metric` lanes
  as command source lanes — it receives observability data from leaves via root relay.

So the actual process map is:
```
root process          ← relay only; no OTLP
business leaves ×4    ← run business nodes; send traces/logs/metrics over IPC lanes
observability_worker  ← receives trace/log/metric from leaves (via root), runs OTLP/JSONL
```

The runner hot path in root and business leaves is **not blocked by OTLP**.
H1 as originally stated is incorrect and has been replaced below.

## Stall hypothesis matrix

### H1 — Direct pipe drain blocking asyncio event loop per scheduler tick (most likely)

**Source:** `IPC transport buffer-drain and scheduler pump model.md` describes this exact
problem. The code confirms it is **not yet fixed**: `PipeExecutionIpcTransportAdapter`
has no background reader thread — `recv()` calls `_recv_from_pipe_buffer()` which calls
`_drain_pipe()` **synchronously in the caller's thread**, which is the asyncio runner.

**Scenario:**
1. Root scheduler tick fires, triggering `system.scheduler.tick` node.
2. Tick node calls `ipc.recv(target)` for each leaf target.
3. `recv()` → `_recv_from_pipe_buffer()` → `_drain_pipe()`:
   - polls OS pipe with `endpoint.connection.poll(timeout)`.
   - If no data: blocks for `poll_interval_seconds = 0.005s` (5ms).
4. With 4 leaf groups + observability_worker = **5 targets**:
   - worst case: 5 × 5ms = **25ms of blocking per scheduler tick**.
5. During these 25ms the asyncio event loop is blocked — cannot process other coroutines.
6. Next scheduler tick is delayed by 25ms; configured cadence is 1ms.
7. Drain rate: at most 1000ms / 25ms = 40 ticks/sec instead of 1000 ticks/sec.
8. At 40 ticks/sec with budget=256: max 10,240 messages/sec drained from all pipes combined.
9. If source generates faster → OS pipe buffers fill → sender threads in leaves block.
10. Blocked sender threads → leaves cannot send results back to root → pipeline stalls.

**Jammed at:** asyncio event loop blocked in synchronous pipe poll inside `recv()`.

**Why visible as "pauses between steps":** each record requires 3 boundary crossings × 2
`recv()` calls per crossing = 6 IPC drains per record. At worst: 6 × 5ms = 30ms of
blocking per record, before any business logic runs.

**Simulation test:** set `poll_interval_ms = 5.0`, `target_count = 5`. Observe effective
tick cadence and end-to-end latency. Then set `poll_interval_ms = 0.0` (background
reader model) and measure difference.

**Fix pointer:** this is the IPC buffer-drain refactor described in the design doc
(`IPC transport buffer-drain and scheduler pump model.md`). The fix:
- background reader thread drains pipe into recv_buffer (not done in `recv()`),
- scheduler pump drains recv_buffer into runner queue (already designed, not implemented).

### H2 — Control-lane budget starvation under data load

**Scenario:**
1. Root scheduler pump drains IPC buffers each tick with budget=256.
2. All 4 leaf data lanes contribute messages simultaneously.
3. Data weight=8 per lane: 4 lanes × 8 × 16 burst = 512 per tick — exceeds budget.
4. Budget exhausted before control lane gets its 4-allotment.
5. Control messages (leaf_hello, ACK signals, stop commands) delayed by 1+ ticks.
6. ACK delay → credits not released → root `_pending` backlog grows → data lane saturates.

**Jammed at:** scheduler pump drain, control starvation.

**Simulation test:** set 4 data-lane leaf processes each generating messages at max rate.
Observe control-lane drain count per tick. Should see it drop to zero intermittently.

**Fix pointer:** enforce control-lane minimum allotment before data drain starts.

### H3 — Post-start-settle quiet window timing

**Scenario:**
1. `post_start_settle_enabled: true`, `quiet_window_seconds: 1.0`.
2. After all leaves are ready, root waits 1 second with no runner activity.
3. During this second, leaves have already started their source nodes and are producing
   messages. These pile up in IPC pipes.
4. After settle, root starts draining 1 second of backlog → sudden spike.
5. Under spike, drain budget exhausted → control lane starved → feedback loop as H2.

**Simulation test:** add 1s settle delay, then inject burst. Observe spike behavior.

### H4 — Tombstone stuck in pending queue (pacing_mode: all)

**Scenario (only in `pacing_mode: all`):**
1. Source emits all messages + tombstone in rapid succession.
2. All messages consume credits → 1024 credits for ingress exhausted.
3. Tombstone enters `_pending` queue behind regular messages.
4. Regular messages get processed → ACKs return → credits refilled → messages flushed.
5. But if one ACK is lost or delayed (e.g., control lane starved per H2), tombstone waits.
6. Shutdown readiness quorum never satisfied → system hangs after processing all records.

**Simulation test:** exhaust credits before tombstone is sent; simulate one dropped ACK.
Observe whether tombstone reaches egress leaf.

**Note:** current config has `batch_max_items: 1` — this limits source to 1 in-flight, so
pacing is already batch mode. H4 is latent risk if batch size is increased.

### H5 — inflight_idle_timeout expiry before processing completes

**Scenario:**
1. `inflight_idle_timeout_seconds: 5.0` — stale inflight entries expire after 5s.
2. Under load, a `BoundaryExecuteCommand` is sent to leaf-B, tracked as inflight.
3. Leaf-B is slow (OTLP blocking, H1). Takes 6 seconds to reply.
4. At t=5s, root expires the inflight entry → logs `control_plane.boundary.inflight_timeout`.
5. Root considers the boundary "done" but no result arrived.
6. Downstream nodes (egress) never receive the result → messages silently dropped.
7. If the record was not tombstone, pipeline keeps running but missing outputs.
8. If it was the only record, downstream looks like it's waiting — appears as a stall.

**Simulation test:** set node latency > 5.0s for one leaf. Observe whether results
arrive at sink, and whether `inflight_timeout` event fires.

**Note:** current `DefaultControlPlaneRootBoundaryHandoffService.has_inflight_deliveries()`
always returns `False` and `drain_completed_deliveries` returns `[]`. The inflight tracking
dict `_inflight` exists but `_expire_stale_inflight()` is never called from the current
dispatch path. So H5 may be inactive — but the stale code suggests it was once relevant.

## Prioritized investigation order

1. **H1 (OTLP blocking)** — known from code analysis; trace_queue is dead code; highest
   probability of being the root cause of observable pauses.

2. **H2 (control starvation)** — hard to observe without metrics; simulation can quantify.

3. **H3 (settle burst)** — easy to test: disable `post_start_settle` and compare runs.

4. **H4 (tombstone in pending)** — low risk with current `batch_max_items: 1`.

5. **H5 (inflight timeout)** — appears dormant in current code.

## What the simulation should instrument

For each hypothesis, the SimPy simulation must expose:

| Metric | H1 | H2 | H3 | H4 |
|---|---|---|---|---|
| Scheduler tick actual cadence vs configured | ✓ | ✓ | ✓ | |
| Control-lane messages drained per tick | | ✓ | | |
| Data-lane pipe queue depth over time | ✓ | ✓ | ✓ | |
| Root `_pending` outbound queue depth | ✓ | ✓ | | ✓ |
| Credit utilization per group | ✓ | | | ✓ |
| Time to tombstone reaching egress | | | | ✓ |
| End-to-end latency P99 | ✓ | ✓ | ✓ | |

## SimPy experiment parameters to vary

```python
# For H1
node_latencies["trace_emit"] = LatencyConfig(kind="deterministic", mean_ms=50)

# For H2
lane_weights = {"control": 4, "data": 8, "trace": 2, "log": 1, "metric": 1}
drain_budget = 64   # lower budget to trigger starvation faster

# For H3
post_start_settle_ms = 1000.0
initial_burst_messages = 100

# For H4
credit_window_size = 10   # small window to exhaust quickly
message_count = 50
```

## Direct config changes to try before simulation

1. **Disable OTLP** (`otel_otlp_topology.enabled: false`) — eliminates H1, measures
   throughput improvement to quantify H1's contribution.

2. **Set `post_start_settle_max_wait_seconds: 0.0`** (already zero) and
   `post_start_settle_quiet_window_seconds: 0.0` — eliminates H3.

3. **Increase `control_poll_ms: 0.5`** — higher tick frequency, harder to starve control.

4. **Disable lifecycle logging** (`lifecycle_events.enabled: false`) — reduces control-lane
   observability traffic and isolates H2.
