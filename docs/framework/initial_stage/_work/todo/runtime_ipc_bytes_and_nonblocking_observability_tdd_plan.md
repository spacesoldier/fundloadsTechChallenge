# Runtime IPC bytes path and non-blocking observability (TDD plan)

## Goal

Close three performance-critical gaps without leaving platform rails:

1. tracing/logging execution must not block business node hot path;
2. supervisor-worker IPC must move away from object `send/recv` (implicit pickle);
3. IPC topology must support split control/data channels (command channel != payload channel).

This plan extends:

- [runtime_nonblocking_and_graceful_stop_audit_plan](runtime_nonblocking_and_graceful_stop_audit_plan.md)
- [web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan](web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md)
- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- [runtime_async_dispatch_loop_template_tdd_plan](runtime_async_dispatch_loop_template_tdd_plan.md)

## Current state (as-is)

- supervisor-worker channel: `multiprocessing.Pipe(duplex=True)` with `send/recv` object transfer;
- stream dispatch mode: one command/response round-trip per boundary item;
- tracing exporters are configured, but direct sink emission can still run synchronously in-process;
- lifecycle logging (stdout/jsonl) is synchronous in supervisor path.

## Target state (to-be)

- business execution groups route observability events to dedicated observability execution group(s);
- observability group uses `AsyncRunner` and async-capable adapters by default;
- control channel and data channel are explicit platform ports/adapters;
- default wire format for IPC commands/data is bytes-framed payload (no implicit pickle path);
- object-channel fallback remains optional and explicit for compatibility only.

---

## Phase A — async tracing path in runtime config

### Scope

- keep current exporter-based external config contract stable and strict-compatible;
- force async OTLP backend profile in config (`httpx` async mode);
- freeze this as baseline before pipeline-only observability migration.

### RED tests

- config validator accepts this contract in strict mode;
- multiprocess smoke run keeps output parity;
- runtime config snapshot keeps `process_supervisor` topology unchanged.

### GREEN implementation

- update `*_multiprocess_jaeger.yml` configs:
  - exporter settings use `backend: httpx` and `httpx.mode: async`.
- allow runtime to auto-materialize trace dispatch rails from enabled exporters,
  without requiring explicit `runtime.observability.pipeline` in user config.

### Exit criteria

- tracing configs are async-backend ready without breaking current strict contract.

---

## Phase B — non-blocking lifecycle logging rail

### Scope

- migrate observability from legacy exporter callbacks to pipeline/system-node rail;
- move supervisor lifecycle event writes from inline sink calls to platform dispatch rail;
- keep console/file exporters but execute through runtime observability pipeline.

### RED tests

- supervisor event emit does not call sink I/O inline on the caller thread;
- lifecycle events are still observable in stdout/jsonl via dispatch nodes;
- stop sequence preserves final lifecycle events before process exit.

### GREEN implementation

- introduce lifecycle event dispatch message model and system node;
- route supervisor/worker lifecycle telemetry via routing queue;
- preserve current log format contracts.

### Exit criteria

- lifecycle logging can be switched off / on / level-tuned without changing blocking profile of hot path.

---

## Phase C — IPC port split: control vs data

### Scope

- explicit platform ports/adapters:
  - `ExecutionControlChannelPort`
  - `ExecutionDataChannelPort`
- separate lifecycles and backpressure for control and payload traffic.

### RED tests

- control channel remains available under data saturation;
- stop command latency unaffected by data throughput;
- misrouted payload cannot be interpreted as control command.

### GREEN implementation

- supervisor uses control adapter for `start/ready/stop/ack`;
- boundary payloads use data adapter only;
- channel ownership and teardown are independent.

### Exit criteria

- no mixed command/data framing on the same logical channel in process supervisor mode.

---

## Phase D — bytes IPC contract (no implicit pickle)

### Scope

- replace object `send/recv` with bytes framing contract in IPC adapters;
- define deterministic codec contract for command envelopes and payload envelopes.

### RED tests

- no use of `Connection.send()` / `Connection.recv()` in active IPC path;
- adapter transmits bytes frames only (`send_bytes` / `recv_bytes` or socket bytes);
- malformed frame rejection is deterministic and categorized.

### GREEN implementation

- implement bytes codec with explicit schema/version field;
- map boundary payload to codec via platform serializer adapter;
- keep compatibility fallback under explicit config flag only.

### Exit criteria

- primary multiprocess runtime path does not rely on implicit pickle serialization.

---

## Phase E — sync-library OTLP on async rails

### Scope

- if exporter backend is sync (`urllib`, `requests`, `urllib3`, `grpcio`, `otel_sdk`),
  run flush off event loop (`asyncio.to_thread`) when processed by async rails.

### RED tests

- async runner event loop is not blocked by sync exporter path;
- exporter failures remain isolated (drop + diagnostics);
- close/flush semantics preserved.

### GREEN implementation

- enforce async wrapper policy inside trace sink adapter;
- add diagnostics counters for offloaded flush operations.

### Exit criteria

- async observability rail remains responsive independent of exporter backend type.

---

## Phase F — regression, perf and rollout

### Scope

- parity: output files and decision determinism unchanged;
- perf: compare stream mode before/after for baseline+experiment;
- docs/runbook update for new config toggles and channel model.

### RED/verification

- targeted regression suites;
- smoke multiprocess + Jaeger runbook checks.

### Exit criteria

- measurable latency reduction in stream mode;
- no graceful-stop regressions;
- clear rollback toggle for bytes IPC path.

---

## Current delta (2026-02-18)

- `system.obs.trace_dispatch` now supports async publish path (`publish_trace_async`) and can run under `AsyncRunner`.
- `TracingObserver` supports async event delivery (`on_trace_event_async`) and uses sink `emit_async` when available.
- `JsonlTraceSink.emit_async` was optimized for batch mode:
  - per-record append stays on-loop (in-memory);
  - thread offload is used only on flush/fsync boundaries.
- OTLP async backend lifecycle was hardened for loop-bound clients:
  - `httpx.AsyncClient`/`aiohttp.ClientSession` are recreated on cross-loop access;
  - sync fallback wrappers enforce timeout-bounded async bridging.
- runner-side inter-hit latency marker (`__runner_gap_ms`) is propagated into trace route metadata and exported as OTLP attributes:
  - `runner_gap_ms`
  - `stream_kernel.runner_gap_ms`
- OTLP payload packing now groups spans by `service.name` into shared `resourceSpans`
  (instead of one resource block per span) to reduce serialization overhead.
- `MultiprocessBootstrapSupervisor.execute_boundary()` now drains pending
  `worker_bootstrapped` control messages before first dispatch, so worker bootstrap
  diagnostics are not delayed until the first command reaches each group.
- jsonl trace records now include flattened route markers (`process_group`,
  `handoff_from`, `route_hop`, `runner_gap_ms`) in addition to nested `route`.

## Current delta (2026-02-19)

- Completed async dispatch loop template rollout for supervisor tracing:
  - contract/spec tracked in
    [runtime_async_dispatch_loop_template_tdd_plan](runtime_async_dispatch_loop_template_tdd_plan.md);
  - per-record `asyncio.run(...)` removed from supervisor trace emission hot path;
  - supervisor trace flush/close now drains dispatch queue before sink flush/close.
- Simplified primitive multiprocess ownership:
  - worker role no longer receives internal tracing flags (`supervisor_only`, `dispatch_via_runner`);
  - worker role does not materialize observability system dispatch nodes;
  - worker still emits trace events via runner boundary path; supervisor remains the only exporter owner.

---

## Notes on batch policy

- This plan does not tune `boundary_dispatch.batch_max_items`.
- Stream model remains primary.
- Batch mode stays compatibility fallback for explicit external integrations only.
