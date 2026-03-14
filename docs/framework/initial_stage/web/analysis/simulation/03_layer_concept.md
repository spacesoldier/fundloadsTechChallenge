# Layer 3 — Concept: pipeline, credits, drain

## Pipeline structure

The fund_load pipeline is a **linear chain** of process groups, each in a separate OS
process, connected by IPC pipes through root as a relay:

```
source (file)
  │
  ▼
[leaf: execution.ingress#1]
  parse_load_attempt → ingress_line_bridge
  │
  ▼ IPC data lane → root → IPC data lane
  │
[leaf: execution.features#1]
  compute_time_keys → idempotency_gate → compute_features
  │
  ▼ IPC data lane → root → IPC data lane
  │
[leaf: execution.policy#1]
  evaluate_policies → update_windows
  │
  ▼ IPC data lane → root → IPC data lane
  │
[leaf: execution.egress#1]
  format_output → egress_line_bridge
  │
  ▼
sink (file)
```

Root is a **relay**: it receives boundary outputs from one leaf and forwards them to the
next leaf's command channel. Root does not execute business logic.

Each IPC hop = send on data lane (source → target_pipe) + drain (scheduler pump) +
runner queue consume.

## Message lifecycle through one boundary

```
t=0  leaf-A runner: node chain produces Envelope(target="next_node_in_leaf_B")
t=1  leaf-A runner: boundary output detected → ipc.send(root_data_lane, envelope)
t=2  root reader thread: receives envelope → recv_buffer
t=3  root scheduler tick: drains recv_buffer → root runner queue
t=4  root runner: LeafIngressNode receives envelope →
       BoundaryHandoffService.drain_external_deliveries([envelope])
t=5  root: BoundaryExecutionService.execute_boundary_on_leaf(leaf-B, inputs)
       → ipc.send(leaf-B_data_lane, ControlPlaneLeafBoundaryExecuteCommand)
t=6  leaf-B reader thread: receives command → recv_buffer
t=7  leaf-B scheduler tick: drains recv_buffer → leaf-B runner queue
t=8  leaf-B runner: LeafCommandService processes command → dispatches to node chain
```

**Total IPC hops per message per boundary crossing: 2 sends, 2 reader drains, 2 scheduler ticks.**

With 4 process groups: ingress → features → policy → egress = 3 boundary crossings = 6 IPC
hops + 6 scheduler ticks per message.

## Credit window: purpose and mechanics

The credit window limits how many messages are **in-flight** at once (sent but not yet
processed by receiver). This prevents OS pipe buffer overflow and memory bloat.

```
window_size: 4096 (total)
groups: ingress(3 nodes) + features(3 nodes) + policy(2 nodes) + egress(3 nodes) + obs(1)
weights: 3:3:2:3:1 = 12 total
allocated per group:
  ingress:  4096 * 3/12 ≈ 1024
  features: 4096 * 3/12 ≈ 1024
  policy:   4096 * 2/12 ≈  682
  egress:   4096 * 3/12 ≈ 1024
  obs:      4096 * 1/12 ≈  341
```

These credits are consumed on **each send** and released when ACK returns. With
`stream_batch_max_items: 1`, each message = 1 credit consumed.

**At 1024 credits for ingress, root can have 1024 messages in-flight to the ingress leaf
before it must wait for ACKs.** Under the fund_load workload (small JSON records, fast
processing), this is unlikely to saturate. But if processing is slow (e.g., OTLP blocking),
ACKs are delayed, and inflight count grows.

## Drain: weighted round-robin

The scheduler pump drains IPC recv_buffers into the runner queue each tick.

Reference policy (from `IPC transport buffer-drain` doc):
```
weights: control=4, data=8, trace=2, log=1, metric=1
per-target burst cap: 16
global per-tick budget: 256
```

At `control_poll_ms: 1.0ms` tick, max throughput = 256 messages/ms = 256,000 msg/sec
**into the runner queue**. Business processing capacity will be much lower.

**Starvation scenario:** if observability messages (trace/log) flood the queue,
drain allotment is `trace=2, log=1` per tick. At 256 total budget, at most 3 obs messages
drained per tick while data gets 8. This is the starvation guard: obs never blocks data.

But: if `drain_budget: 256` is consumed entirely by data messages (8-weight × 16 burst
× N targets), control lane messages (hello, ACK, stop) might not get their 4-allotment
if budget runs out first. **This is a real risk under heavy data load.**

## Source pacing: batch vs all

`pacing_mode: batch` (effective from config `batch_max_items: 1`):
- Source emits 1 message.
- Waits for `sink_dispatch_ack` (signal from egress leaf that a message was dispatched).
- Then emits the next.

This creates a **pipeline backpressure** from sink back to source:
- If egress leaf is slow → sink_dispatch_ack is delayed → source pauses.
- End-to-end throughput is bounded by the slowest stage.
- Buffer utilization stays low (at most one message per stage in-flight).

`pacing_mode: all` removes this constraint: source fires all messages immediately.
Buffers fill up proportionally to processing latency.

## Tombstone propagation

Tombstone = a message with `envelope.tombstone=True`, emitted by source after file is exhausted.

Tombstone path:
1. Source emits tombstone on data lane.
2. Each leaf receives tombstone, passes it through node chain unchanged.
3. Egress leaf dispatches tombstone to sink.
4. When all leaves have propagated tombstone, `shutdown_readiness` quorum is satisfied.
5. Root calls `request_stop()` on all leaves.

**Stall: if tombstone enters the `_pending` credit queue** (credits exhausted), it waits
behind regular data messages. If there are no more data messages to generate ACKs, the
tombstone is stuck forever. This is not a problem in `pacing_mode: batch` (at most 1 message
in-flight) but in `pacing_mode: all` under heavy load it can occur.

## Conceptual stall catalogue

| Name | Trigger | Where it jams |
|---|---|---|
| **credit-pending deadlock** | Both sides need credits from each other | root and leaf both in `_pending` queue |
| **scheduler-starvation stall** | Runner queue never empty | ticks don't fire; drain stops; pipe fills |
| **OS-pipe saturation** | Payload size × message rate > 64KB buffer | sender thread blocks on `os.write` |
| **ACK-lag inflight growth** | Slow OTLP / logging in leaf runner | credits released slowly; pending grows |
| **tombstone in pending** | All credits consumed before tombstone | shutdown never completes |
| **control-lane budget-starvation** | Data load exhausts drain budget | stop/hello/ack messages delayed |
