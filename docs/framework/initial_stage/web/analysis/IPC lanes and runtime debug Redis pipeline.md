# IPC lanes and runtime debug Redis pipeline

Status: implemented baseline (March 2026), with explicit follow-up for generic Redis carriers.

## Purpose

Document the current multiprocess communication model after lane split and
runtime debug instrumentation refactor:

- lane-separated IPC between root and leaf processes;
- platform-rails debug pipeline (`node -> service -> adapter`) in each process;
- Redis-backed debug sink behavior and key model.

---

## 1) Inter-process IPC lane model

Current lane set is explicit in transport contracts:

- `control`
- `data`
- `trace`
- `log`
- `metric`

Reference:

- `src/stream_kernel/execution/transport/ipc/ipc_transport.py`
- `src/stream_kernel/execution/transport/ipc/ipc_lane_routing_service.py`

### 1.1 Addressing

Worker target id is lane-aware:

- control lane: `<worker_id>`
- non-control lanes: `<worker_id>|<lane>`

Helper contracts:

- `compose_execution_ipc_worker_target_id(...)`
- `decompose_execution_ipc_worker_target_id(...)`
- `execution_ipc_worker_lane_targets(...)`

### 1.2 Lane resolution

Lane is selected by `ExecutionIpcLaneRoutingService` using:

1. payload type map
2. target prefix map
3. fallback target heuristic

Defaults include:

- `system.cp.* -> control`
- `system.obs.trace* -> trace`
- `system.obs.log* / system.obs.debug* -> log`
- `system.obs.metric* / system.obs.monitor* -> metric`
- business payloads default to `data`

`preload_snapshot(...)` allows loading deterministic route tables at startup
without exposing lane mapping to user config as an unstable public contract.

---

## 2) Handoff and replay behavior

Boundary/handoff dispatch is lane-aware and runs through transport services
(not direct pipe calls from business nodes).

Reference:

- `src/stream_kernel/execution/transport/handoff/ipc_handoff_dispatch_service.py`
- `src/stream_kernel/execution/transport/handoff/replay.py`

Important current behavior:

- replay path skips re-enqueue of `system.obs.*` envelopes into root runner loop;
- this avoids observability replay self-loops in root.

---

## 3) Runtime debug message flow (platform rails)

## 3.1 Producers

Debug messages are generated from:

- injected port wrappers (`inject_debug`), for stream/kv/queue/topic/ipc ports;
- service public method decorators (`debug_instrument_service_methods`);
- root lifecycle/runtime debug emitters.

Port debug explicitly skips `LogMessage` and `DebugMessage` ports to avoid
recursive self-observation.

Reference:

- `src/stream_kernel/application_context/inject_debug.py`
- `src/stream_kernel/platform/services/runtime/debug_buffer.py`

## 3.2 Buffer and routing

Per-process runtime debug buffer collects `DebugMessage` records.

Runner drains this buffer and routes drained `DebugMessage` objects through the
regular router path, so they are consumed by system debug nodes.

Reference:

- `src/stream_kernel/execution/runtime/runner.py`

## 3.3 Consumer side in each process

When `redis_debug` exporter is enabled, runtime materializes:

- node: `system.debug.message_dispatch`
- service: `RuntimeDebugMessageDispatchService`
- stream sink: `stream<DebugMessage>` (bound to debug adapter instance)

This executes in every participating process (root and leaf), so debug records
can be published locally without mandatory relay through `system.observability`.

Reference:

- `src/stream_kernel/execution/orchestration/debug_system_nodes.py`
- `src/stream_kernel/platform/services/runtime/debug_message_dispatch.py`
- `src/stream_kernel/execution/orchestration/builder.py`

## 3.4 Leaf lifecycle debug path

Leaf lifecycle debug (`leaf_debug_log`) must not maintain an isolated in-memory
list with manual locking as a separate transport path.

Current contract target:

- lifecycle emits structured `DebugMessage` records;
- records are published one-way into process-local runtime debug buffer service;
- runner drains buffer and routes records through `system.debug.message_dispatch`
  to the configured debug adapter (`debug_redis` stream sink);
- optional file output remains a secondary sink controlled by
  `runtime.platform.debug.leaf_debug_write_to_file`.

This keeps leaf startup/runtime debug on the same platform rails as the rest of
runtime debug instrumentation.

---

## 4) Redis adapter status: platform vs specialized

Short answer: **yes, it is a platform adapter**, but currently **specialized**.

Current adapter:

- `@adapter(name="debug_redis", binds=[("stream", DebugMessage)])`
- module: `src/stream_kernel/observability/adapters/debug.py`

Why this is platform-compliant:

- discovered by framework adapter registry;
- built via runtime exporter config;
- injected through ports (no direct helper wiring in business code).

Why it is not yet generic Redis transport:

- contract is fixed to `DebugMessage` stream;
- key schema is debug-run oriented (run/process indexes, summaries);
- it does not expose generic `kv`, `queue`, or `kv_stream` Redis carrier
  contracts for business/control transport.

---

## 5) Redis debug key model (current)

Adapter writes:

- per-run/per-process debug lists
- run metadata hash
- run/process indexes (`ZSET` + `HASH`/`SET`)

Key family (prefix default `stream_kernel:debug`):

- `:<prefix>:runs:<run_id>:debug:<process_id>`
- `:<prefix>:runs:<run_id>:processes`
- `:<prefix>:runs:<run_id>:processes:by_time`
- `:<prefix>:runs:meta:<run_id>`
- `:<prefix>:runs:index:by_time`
- `:<prefix>:debug:index:process`
- `:<prefix>:debug:index:by_time`

This allows run-centric and process-centric debug retrieval.

---

## 6) Config surface (runtime)

Enable direct Redis debug pipeline via logging exporters:

```yaml
runtime:
  observability:
    logging:
      exporters:
        - kind: redis_debug
          mode: all
          settings:
            host: 127.0.0.1
            port: 6379
            db: 0
            key_prefix: stream_kernel:debug
            ttl_seconds: 86400
```

No separate user-facing lane mapping section is required for this.

---

## 7) Planned follow-up (recommended)

To reuse Redis beyond debug, introduce **generic platform Redis carriers** as a
separate contract family (for example under execution transport carriers):

- `redis_kv_store` (`kv`)
- `redis_queue` (`queue/topic`)
- `redis_kv_stream` (for transport lanes where needed)

Keep `debug_redis` as domain-specific adapter on top of those carriers.

This preserves current debug behavior while opening Redis for broader platform
state and transport backends.
