# Runtime non-blocking observability and graceful stop — audit and closure plan

## Audit context (2026-02-15)

Four problems were identified in the runtime execution path. This plan closes each one
using only platform-native mechanisms: @node, @service, ports/adapters, router, work queue.
No ad-hoc background threads or queues are introduced outside these rails.

Follow-up execution track (2026-02-18):

- [runtime IPC bytes + non-blocking observability plan](runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md)
- [observability backpressure + Prometheus exporter plan](observability_backpressure_and_prometheus_exporter_tdd_plan.md)

---

## Problem 1 — Observability execution bypasses platform graph

### What the audit found

**a) Sink emission is inline in the runner hot path.**

`TracingObserver.after_node()` calls `self._sink.emit(record)` directly and synchronously
inside `SyncRunner.run()` (and equivalently in `AsyncRunner`). The runner loop is blocked
for the entire duration of the I/O: network call, file write, stdout flush. This is why
trace times are large — every business record execution includes the full OTLP/file I/O.

**b) `ObservabilityDispatchNode` is not a @node.**

`observability_system_nodes.py` defines `ObservabilityDispatchNode` as a plain `@dataclass`
with `__call__`, bypassing the node registry entirely. It is inserted as a raw `StepSpec`
in `builder.py`. Discovery cannot find it, `plan_pools()` cannot inspect it, async
capability propagation (Phase J.1) cannot detect it. The inject marker
`inject.service(ObservabilityPipelineService, qualifier=...)` set in `__post_init__`
is never resolved by the DI scan — it is passed as a constructor argument instead.

**c) Concrete pipeline services are not @service.**

`FanoutObservabilityService` and `ReplyAwareObservabilityService` are plain dataclasses.
Only the no-op stub `NoOpObservabilityService` carries `@service(name="observability_service")`.
Concrete implementations are wired manually in the builder, outside DI.

**d) `TraceSinkLike` is a local protocol, not a platform port.**

The `TraceSinkLike` protocol (`emit / flush / close`) lives inside
`observability/observers/tracing.py` as a private type. It is not declared as a platform
port, has no `@adapter`-annotated implementations in the registry, and is not visible to
adapter discovery or async capability propagation.

### Target model

Observability execution must be part of the graph, not a side effect of the runner.

```
Runner executes business node
  └─ TracingObserver.after_node()
       └─ routes TraceRecord into work queue          ← non-blocking (in-memory enqueue)

Work queue delivers TraceRecord to trace sink node
  └─ @node trace_sink
       └─ inject.port(TraceSinkPort)
            └─ adapter.emit(record)                   ← I/O here, isolated in its own node
```

The runner hot path only pays the cost of in-memory routing, not the I/O itself. All I/O
happens when the runner processes the trace node's turn — exactly like any other node.
Graceful stop via `drain_on_stop=True` naturally drains pending trace events too, so no
separate flush protocol is needed for tracing.

Ports and adapters:

- Platform port: `TraceSinkPort` (with `emit`, `flush`, `close` contract)
- Adapters implement `TraceSinkPort` and declare `execution_mode` (`sync` / `async`)
- Async adapters (httpx-async, aiohttp) trigger async capability propagation automatically

---

## Problem 2 — Graceful stop: SyncRunner has no stop signal

### What the audit found

`SyncRunner.run()` is a bare `while True` loop that calls `work_queue.pop()` returning
`None` when empty, then breaks. There is no stop flag, no signal handling between
iterations. SIGTERM during execution terminates the process mid-iteration, possibly after
a business node has run but before its outputs are routed — losing data.

`AsyncRunner` has `_stop_requested` / `drain_on_stop` which correctly drains the work
queue. This mechanism is correct; SyncRunner needs the same.

### Additional gap: output file flush in multiprocess mode

The multiprocess supervisor waits for the IPC boundary (TCP star) to drain before sending
stop. But the child process still needs to:
1. Drain the work queue (including trace event messages from Problem 1 fix)
2. Flush and close the output file sink (`on_run_end`)
3. Ack completion

The current graceful timeout (`graceful_timeout_seconds`) must include time for both queue
drain and file flush. If forced termination fires before `on_run_end()`, the file may be
incomplete. No explicit "output closed" signal is sent from child to supervisor.

### Required fixes

1. Add `_stop_requested` + `drain_on_stop` to `SyncRunner` mirroring `AsyncRunner`.
2. Wire stop signal from lifecycle manager to runner (currently only wired for async).
3. In multiprocess mode: supervisor waits for child `on_run_end` completion signal before
   considering the child stopped, not just for the IPC boundary to drain.
4. Tighten stop-command semantics in supervisor/worker control-plane:
   - `stop_ack` is considered successful only when explicitly received (timeout is not success).
   - Worker must send `stop_ack` only after child runtime close (`on_run_end` + scope close) completes.
   - `stop_ack` must carry explicit `output_closed=true` confirmation.
   - `drain_inflight=True` stop-command timeout should use remaining graceful budget, not a fixed tiny timeout.
   - Lifecycle order must be `wait_boundary_drain -> stop_groups -> wait_output_closed`.
   - Supervisor lifecycle logs must emit `worker_output_closed` when stop handshake confirms output close.

TDD cases for item 4:
- `test_p5pre_sup_08b_worker_sends_stop_ack_only_after_runtime_close`
- `test_p5pre_sup_12b_drain_inflight_stop_command_uses_graceful_budget`
- `test_p5pre_sup_12c_stop_timeout_is_not_treated_as_stop_ack`

---

## Problem 3 — asyncio is island-based: each HTTP call spawns a new event loop

### What the audit found

`_run_async_blocking()` in `trace_sinks.py` runs `asyncio.run()` or spawns a daemon thread
with its own event loop for every `_flush_batch()` call. When called from inside
`AsyncRunner` (which already runs in an event loop), the "nested loop" detection triggers
a daemon thread. Each httpx-async or aiohttp export creates an independent event loop
lifecycle. This is expensive and defeats the purpose of async.

### Correct fix (falls out of Problem 1 fix)

Once `TracingObserver` routes trace events into the work queue and the trace sink node runs
as a proper graph node under `AsyncRunner`, the sink adapter's `emit()` can be an
`async def` coroutine awaited directly by the runner — no wrapper, no separate loop, no
thread. `_run_async_blocking()` is retained only in `close()` / `flush()` paths (called
once at shutdown, not in the hot path).

---

## Implementation phases

### Phase A — Platform port: TraceSinkPort

Scope: declare `TraceSinkPort` as a platform port, move implementations to `@adapter`.

**Step A1 RED**

Tests:
- `test_trace_sink_port_is_platform_port`: `TraceSinkPort` is discoverable by adapter
  discovery scan.
- `test_trace_sink_adapter_declares_execution_mode`: each trace adapter (`trace_jsonl`,
  `trace_stdout`, `trace_otel_otlp`, httpx-async, aiohttp) declares `execution_mode` in
  `@adapter` metadata.
- `test_async_trace_adapter_triggers_async_capability`: adapter with
  `execution_mode="async"` causes injecting node to be marked async-capable by
  `plan_pools()`.

**Step A2 GREEN**

- Move `TraceSinkLike` definition to `adapters/contracts.py` or a new `ports/trace.py`
  as a named platform port.
- Annotate each trace adapter factory with `@adapter(execution_mode=...)`.
- `TracingSinkPort` binding registered via adapter discovery, not manually wired in builder.

### Phase B — @node for trace sink, @service for pipeline services

Scope: make every observability execution unit a first-class platform citizen.

**Step B1 RED**

Tests:
- `test_trace_sink_node_is_in_node_registry`: node registry scan finds `system.obs.trace_sink`.
- `test_trace_dispatch_node_is_in_node_registry`: node registry finds all four dispatch
  kinds (`trace_dispatch`, `log_dispatch`, `metric_dispatch`, `monitor_dispatch`).
- `test_fanout_service_is_service_decorated`: DI scan resolves `FanoutObservabilityService`
  via `@service`.
- `test_reply_aware_service_is_service_decorated`: DI scan resolves
  `ReplyAwareObservabilityService` via `@service`.
- `test_obs_dispatch_node_async_capability_via_port`: when injected `TraceSinkPort` adapter
  is async, `plan_pools()` marks the dispatch node as async.

**Step B2 GREEN**

- Decorate `ObservabilityDispatchNode` (or a refactored successor) with `@node`.
- Create `TraceSinkNode` (`@node(name="system.obs.trace_sink")`) with
  `inject.stream(TraceSinkPort)`.
- Decorate `FanoutObservabilityService` and `ReplyAwareObservabilityService` with
  `@service`.
- Remove manual `StepSpec` insertion in `builder.py`; system nodes discovered through
  standard registry path.

**Step B3 — Recursion guard: TracingObserver excludes system nodes**

System nodes must not generate trace events for their own execution. Otherwise
`trace_sink_node` processing a `TraceRecord` would generate another `TraceRecord`,
causing infinite recursion.

Fix:
- Add `excluded_node_names: frozenset[str]` field to `TracingObserver`.
- `before_node()` returns `None` immediately if `node_name in excluded_node_names`.
- Since `state is None`, `after_node()` / `on_node_error()` are already no-ops (existing check).
- Builder collects all system node names from `ObservabilitySystemPlan.system_node_names`
  and passes them when constructing `TracingObserver`.

Tests to add:
- `test_tracing_observer_skips_excluded_nodes`: call `before_node()` / `after_node()` for
  an excluded node name — `sink.emit()` must not be called.
- `test_builder_passes_system_node_names_to_tracing_observer`: builder constructs
  TracingObserver with `excluded_node_names` containing all system.obs.* node names.

Note: `execution_mode` on trace adapters is intentionally accurate:
- `sync` adapters (urllib, requests, urllib3, grpcio, otel_sdk): genuinely blocking I/O;
  marking them `"async"` would be false and would block an event loop if placed in AsyncRunner.
  After Phase C they are non-blocking for the business process: they run in a separate
  process group or in a separate runner turn. Phase E adds `asyncio.to_thread()` wrapping
  so they can safely live in AsyncRunner without stalling the loop.
- `async` adapters (httpx-async, aiohttp): correctly marked; Phase E will `await emit_async()` directly on the runner loop.

### Phase C — Routing-native trace emission (remove inline sink.emit)

Scope: `TracingObserver.after_node()` routes `TraceRecord` as a message instead of calling
`sink.emit()` directly. The trace sink node processes it.

**Step C1 RED**

Tests:
- `test_trace_after_node_routes_message_not_emits`: after `after_node()`, `sink.emit()`
  is NOT called; work queue has a `TraceRecord` message.
- `test_trace_sink_node_processes_trace_record`: runner iteration with `TraceRecord` in
  queue results in `sink.emit()` called once.
- `test_trace_hot_path_returns_before_io`: timing test — `after_node()` returns in
  microseconds; I/O happens separately in a subsequent runner iteration.

**Step C2 GREEN**

- `TracingObserver.after_node()` constructs `TraceRecord` and calls
  `work_queue.push(Envelope(payload=record, target="system.obs.trace_sink"))`.
- `TracingObserver` requires `QueuePort` and `RoutingService` injections (or a minimal
  "emit to queue" port).
- `TracingObserver.on_run_end()` no longer calls `sink.flush()` / `sink.close()` directly;
  these are triggered by the sink node's own teardown or a lifecycle drain signal.

**Step C3 GREEN: same treatment for logging observer**

- `LoggingObserver` (stdout / JSONL) gets the same routing treatment:
  emits `LogRecord` message → `system.obs.log_sink` node processes it.
- stdout write cost moves out of the hot path.

**Step C4 regression**

- End-to-end parity: same records in output file and trace log.
- Hot-path latency reduction visible in trace spans.

### Phase D — SyncRunner stop signal

Scope: add drain-on-stop to SyncRunner; fix multiprocess output-closed ack.

**Step D1 RED**

Tests:
- `test_sync_runner_respects_stop_between_iterations`: stop flag set while runner is
  between iterations → runner exits cleanly; current item completed, no partial record.
- `test_sync_runner_drain_on_stop_empties_queue`: with `drain_on_stop=True`, all queued
  items processed before exit.
- `test_multiprocess_output_flush_before_forced_kill`: simulated slow child still writes
  all records if stop timeout allows it; forced kill only after flush ack or timeout.

**Step D2 GREEN**

- Add `_stop_requested: bool` and `drain_on_stop: bool` to `SyncRunner`.
- Lifecycle manager sends stop signal to both `SyncRunner` and `AsyncRunner` via same
  interface.
- Multiprocess: child sends an explicit "output closed" message (via control plane)
  after `on_run_end()` completes; supervisor uses this to confirm graceful completion
  rather than inferring from IPC drain alone.

### Phase E — Retire _run_async_blocking from hot path

Scope: eliminate per-call event loop creation for async trace adapters.

**Step E1 RED**

Tests:
- `test_async_trace_adapter_no_extra_thread`: under `AsyncRunner`, trace adapter `emit()`
  does not spawn a new thread; `asyncio.all_tasks()` count does not grow.
- `test_async_trace_adapter_awaited_on_runner_loop`: adapter `emit_async()` is awaited
  directly by the runner when processing a `TraceRecord` envelope.

**Step E2 GREEN**

- Introduce `async def emit_async(record)` on async trace adapters.
- Runner dispatches to `emit_async()` when adapter is detected async-capable and runner
  is `AsyncRunner`.
- `_run_async_blocking()` remains only in `close()` / `flush()` (shutdown paths).

### Phase F — docs sync and sign-off

- Update `Tracing runtime.md`: primary path is now graph-routed trace emission.
- Update `Execution runtime and routing integration.md`: runner no longer owns sink lifecycle.
- Update `Ports and adapters model.md`: `TraceSinkPort` declared as platform port.
- Commit closure report.

---

## Execution status

- [x] Phase A — TraceSinkPort as platform port
- [x] Phase B — @node/@service rails for observability
- [x] Phase C — routing-native trace emission
- [x] Phase D — SyncRunner stop signal + multiprocess output ack
- [x] Phase E — retire _run_async_blocking from hot path
- [x] Phase F — docs sync

---

## Priority order

1. **Phase C** + **Phase B** together — core correctness: removes blocking from hot path
   and puts observability on platform rails. These two are coupled (Phase B defines the
   nodes that Phase C routes to).
2. **Phase A** — enables async capability propagation for trace adapters; needed before
   Phase E makes sense.
3. **Phase D** — graceful stop: prevents data loss on shutdown.
4. **Phase E** — performance refinement: makes async trace adapters truly loop-native.
5. **Phase F** — documentation closure.

---

## Notes

- No daemon threads, no external queues, no custom schedulers are introduced.
  The work queue IS the queue. The runner IS the scheduler. The router IS the dispatcher.
- `_run_async_blocking()` is not removed wholesale — it is valid for shutdown flush paths
  where one-time blocking is acceptable.
- `TracingObserver` may be simplified or replaced by a lightweight "span record builder"
  once sink emission is routed. The observer pattern (before_node/after_node state machine)
  is still needed for span timing; only the sink call moves out.
- The same pattern (Phase B + C) applies to logging and monitoring observers.
