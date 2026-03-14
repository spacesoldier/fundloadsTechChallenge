# Layer 2 — Framework: stream_kernel abstractions

## Object model

```
@node       ← pure callable, DI-injected dependencies, no lifecycle
@service    ← has start()/stop(), optional background loop
@adapter    ← I/O boundary (source, sink, ipc pipe), execution_mode=sync|async
```

The framework builds a **graph** of nodes and services from the YAML config using
discovery (`discover_nodes`, `discover_services`, `discover_adapters`).
Execution is driven by the **runner**, which dispatches messages to nodes via
the **routing** layer.

## Runner

`AsyncRunner` is the primary runtime path. It owns an asyncio event loop and runs:

1. `runner_loop()` — pulls `Envelope` objects from the runner queue, dispatches to nodes.
2. `request_stop()` — cooperative stop; drains inflight before exiting.

The runner loop is event-driven: it `await`s on a queue get, then processes synchronously
through the node chain. There is no yield between nodes in the same process group.

Implication for stall detection: if one node blocks (sync call, lock, or slow I/O),
**all subsequent nodes in that process group stall** until it returns.

## Scheduler

`PlatformSchedulerService`:
- Timer-based: fires a `PlatformSchedulerTickEvent` on the runner queue on idle.
- "On idle" = runner queue is empty after draining a batch of messages.
- The tick event triggers all registered `system.scheduler.*` nodes.

Key scheduler nodes (root + leaf):
- `system.scheduler.tick` → dispatches `IpcTransportIngress*` jobs.
- `system.scheduler.command` → checks for control-plane commands (stop, leaf_hello, etc.).

The scheduler is **not** a wall-clock timer with guaranteed cadence. It fires when the
runner queue is empty — so under sustained load, tick cadence degrades.

**Stall implication:** if business messages arrive continuously, the runner queue is never
empty, ticks stop firing, IPC drain stops, messages pile up in the OS pipe buffer, which
eventually blocks the sender thread.

## IPC transport service (coordinator)

`ExecutionIpcTransportCoordinatorService` is the interface between the asyncio runner
and the threading-based `PipeExecutionIpcTransportAdapter`:

```
runner coroutine
  └─ ipc.send(target, payload)
       └─ coordinator._try_send_now()       ← try_acquire credits, enqueue to pipe
       └─ coordinator._enqueue_pending()    ← if credits exhausted
asyncio idle
  └─ scheduler tick
       └─ system.scheduler.tick node
            └─ coordinator.recv(target)     ← drain recv_buffer → runner queue
```

Flow-control ACK release is asynchronous (reader thread callback), so credit availability
lags behind actual message processing on the receiver side.

## Control-plane services (root side)

```
ControlPlaneRootBoundaryHandoffService
  ├─ drain_external_deliveries(envelopes)
  │    └─ _dispatch_external_chunk()
  │         └─ BoundaryExecutionService.execute_boundary_on_leaf()
  │              └─ ipc.send(data_lane_target, ControlPlaneLeafBoundaryExecuteCommand)
  └─ _pending_dispatch deque            ← backlog of envelope chunks
```

`drain_external_deliveries` is called from root system nodes when boundary outputs arrive
from a leaf. It immediately forwards them to the next leaf via IPC data lane.

`dispatch_max_envelopes_per_tick: 128` — at most 128 envelopes dispatched per tick.
`stream_batch_max_items: 1` — one envelope per IPC send call (from config).

So: N envelopes = N IPC sends = N credit acquisitions. Under high load, the pending_dispatch
deque grows if credits are exhausted.

## Leaf command service

`LeafCommandService` (leaf side):
- Listens on control lane for `ControlPlaneLeafBoundaryExecuteCommand`.
- On receive, dispatches command inputs to the leaf runner queue for execution.
- After execution, sends boundary results back to root over data lane.
- After each send, increments `sent_count` and may send ACK control signal back.

The timing of ACK depends on how often the leaf's runner loop processes commands and how
quickly the leaf's sender thread can write to the OS pipe.

## Boundary handoff: stream mode (config: `mode: stream`)

In stream mode (`boundary_dispatch.mode: stream`):
1. Root sends `BoundaryExecuteCommand` to leaf — no reply expected immediately.
2. Leaf processes and streams results back (each result is an IPC data send to root).
3. Root's leaf-ingress service receives results and routes them to root runner queue.
4. There is no synchronous request-reply round trip.

`wait_for_result: False` in `execute_boundary_on_leaf()` confirms this — fire-and-forget.

**No per-message ack from root to leaf on boundary completion.**
The only feedback is the credit ACK (flow control signal), not a business-level ack.

## Readiness and tombstone

Readiness barrier: all leaf workers must signal `leaf_ready` before root emits start-work.
`start_work_on_all_groups_ready: true` — this is the startup gate.

Tombstone: a special envelope with `tombstone=True`. When the source is exhausted, it emits
a tombstone. It propagates through the entire node chain. When all leaves have seen a
tombstone, shutdown quorum is reached and `request_stop()` is called.

**Stall implication:** if tombstone is dropped (e.g., observability lane discards it because
`drop_policy: non_block` and the queue is full), shutdown never completes. But tombstones go
on the data lane, not observability — so this should be safe. However, if tombstone gets
stuck in the `_pending` queue of a saturated credit window, it waits behind data messages.

## Observability dispatch

`DispatchingObservabilityService`:
- Owns a background `asyncio.Queue` (service_worker queue, `max_items: 131072`).
- Observer calls (`emit_trace`, `emit_log`) push to this queue — **non-blocking**.
- Background worker drains and forwards to exporters.

**This should not cause stalls** — `drop_policy: non_block` means messages are discarded
if the queue is full. However, the background worker is another asyncio task in the same
event loop — it competes with the runner for CPU time.

## Summary: framework-level stall surfaces

| Surface | Root cause |
|---|---|
| Scheduler tick starvation | Runner queue never empty; ticks don't fire |
| `_pending_dispatch` deque growth | Credits exhausted; boundary commands queued |
| Leaf command queue backlog | Leaf runner slow; data lane backs up |
| Credit ACK latency | Reader thread slow or control lane congested |
| Tombstone in credit-pending queue | Shutdown blocked waiting for credits |
