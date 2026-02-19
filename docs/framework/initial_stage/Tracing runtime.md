# Tracing runtime (newgen)

This document defines how runtime tracing is configured and wired in the
newgen runtime, using platform DI/discovery rails.

---

## 1. Goals

- Keep tracing configuration declarative in runtime config.
- Route exporter activation through framework adapter discovery/registry only.
- Keep external tracing config stable under `runtime.observability.tracing.exporters`.
- Execute exporter I/O through runtime observability rails (dispatch/system nodes), without
  blocking business node callbacks.
- Preserve strict/non-strict deterministic behavior for missing bindings.

---

## 2. Primary config model

Primary external tracing contract is exporter-based:
`runtime.observability.tracing.exporters[]`.

```yaml
runtime:
  observability:
    tracing:
      exporters:
        - kind: otel_otlp
          settings:
            otlp:
              endpoint: http://127.0.0.1:4318/v1/traces
            transport:
              backend: httpx
              batch:
                max_items: 64
                flush_interval_ms: 200
            service:
              service_name: stream-kernel
```

### 2.1 Tracing exporters

`runtime.observability.tracing.exporters[]` supports:

- `kind: jsonl`
- `kind: stdout`
- `kind: otel_otlp` (generic OTLP exporter)
- `kind: otel_otlp_logical` (logical pipeline view preset)
- `kind: otel_otlp_topology` (process-topology view preset)
- `kind: opentracing_bridge`

`runtime.observability.logging.exporters[]` supports:

- `kind: stdout`
- `kind: stdout_plain`
- `kind: jsonl`
- `kind: file_plain`

Exporter entry fields:

- `enabled: true|false` — enable/disable this exporter instance.
- `backend` — OTLP backend implementation selector (OTLP kinds only).
  Also supported in grouped form: `settings.transport.backend`
  (legacy compat: `settings.backend`).
- `settings.trace_view: logical|topology` — explicit trace-view marker attached
  to emitted spans (`stream_kernel.trace_view`).
- `settings.isolate_view_ids: true|false` (OTLP only) — when `true`, exporter
  derives view-specific IDs (`trace_id@<view>`, view-specific span/parent ids)
  so logical and topology streams do not merge into one trace in Jaeger.
  Default in presets: `true` for `otel_otlp_logical` and `otel_otlp_topology`.
- `settings.logical_include_platform_spans: true|false` (OTLP only) — when
  `trace_view=logical`, controls whether platform/system spans
  (`system.obs.*`, `supervisor.transport`) are included.
  Default: `false`.
- `settings.topology_include_business_spans: true|false` (OTLP only) — when
  `trace_view=topology`, controls whether business node spans are included.
  Default: `true` for generic `otel_otlp`; preset `otel_otlp_topology` sets it
  to `false` so topology view focuses on platform transport hops.
- `settings.include_runtime_resource: true|false` — include host/process/runtime
  resource attributes (for example `host.name`, `process.pid`,
  `process.runtime.*`) in OTLP resource section.
- `settings.trace_slice: all|business_logic|platform_internals` (JSONL only) —
  writes selected trace plane to file. `business_logic` excludes platform
  control spans (`system.obs.*`, supervisor transport hops);
  `platform_internals` includes only platform control/transport spans.
  Aliases are accepted:
  `full->all`, `logical->business_logic`, `topology->platform_internals`.
  Grouped alias form is also accepted: `settings.view.trace_view`.

Logging file exporters (`jsonl`, `file_plain`) support:

- `settings.path` (optional): explicit file path.
- `settings.file_prefix` (optional): used for generated filename when `path` is not set.
- `settings.flush_every_n` (optional, `>0`): flush cadence.
- `settings.fsync_every_n` (optional, `>0`): fsync cadence.

Logging exporters also support:

- `mode: lifecycle|all` (optional, default `lifecycle`):
  - `lifecycle` — controlled by `runtime.observability.logging.lifecycle_events`.
  - `all` — exports all supervisor runtime events regardless of lifecycle level gate.

If `path` is omitted, runtime generates a sortable UTC filename:
`logs/<prefix>_<YYYYMMDDTHHMMSSZ>_pid<PID>.(jsonl|log)`.

Lifecycle logging is single-writer in multiprocess mode:

- all lifecycle exporters are owned by supervisor process;
- workers forward lifecycle events over control channel only;
- worker-local file sinks are disabled (`settings.workers_dir` is not supported).

OTLP span attributes include:

- `stream_kernel.correlation_id` — stable base request id for cross-view join.
- `stream_kernel.trace_view` / `stream_kernel.trace_plane` — view marker.
- `stream_kernel.parent_resolution` when parent was relinked to root in filtered
  views (`filtered_to_root` / `unknown_to_root`) to avoid dangling incomplete
  parent references.

Message lifecycle platform spans (multiprocess mode):

- `system.obs.worker_boundary_receive` — worker accepted boundary input.
- `system.obs.worker_boundary_emit` — worker emitted boundary outputs back to supervisor.
- `system.obs.supervisor_boundary_receive` — supervisor received worker boundary output.
- `system.obs.supervisor_boundary_dispatch` — supervisor dispatched message to target worker group.

These spans are emitted by supervisor-owned tracing exporters. Workers forward
message-lifecycle records over control channel; workers do not own trace/log sinks.

`otel_otlp` backends currently supported by validator/runtime:

- `urllib`
- `requests`
- `httpx`
- `aiohttp`
- `urllib3`
- `grpcio`
- `otel_sdk`

Dual-slice pattern (both views at once):

```yaml
runtime:
  observability:
    tracing:
      exporters:
        - kind: otel_otlp_topology
          enabled: true
          settings:
            otlp:
              endpoint: http://127.0.0.1:4318/v1/traces
            transport:
              backend: httpx
            service:
              service_name: stream-kernel
            view:
              include_runtime_resource: true
        - kind: otel_otlp_logical
          enabled: true
          settings:
            otlp:
              endpoint: http://127.0.0.1:4318/v1/traces
            transport:
              backend: httpx
            service:
              service_name: stream-kernel
            view:
              include_runtime_resource: true
```

JSONL dual-slice pattern (two files, same run, same request correlation ids):

```yaml
runtime:
  observability:
    tracing:
      exporters:
        - kind: jsonl
          settings:
            path: trace_business.jsonl
            trace_slice: business_logic
        - kind: jsonl
          settings:
            path: trace_platform.jsonl
            trace_slice: platform_internals
```

Backend resolver order for `otel_otlp*` exporters:

1. `exporter.backend`
2. `exporter.settings.transport.backend`
3. `exporter.settings.backend` (compat)
4. default `urllib`

---

### 2.2 Optional internal pipeline config

`runtime.observability.pipeline` may be provided for explicit internal dispatch
customization (for example qualifiers and additional observability streams), but
this is not required for tracing export activation.

When `runtime.observability.tracing.exporters[]` is configured, runtime
materializes `system.obs.trace_dispatch` automatically and routes trace events
through `ObservabilityPipelineService`.

If `runtime.observability.pipeline.system_nodes` is provided explicitly, this
list is authoritative for dispatch activation (including explicit disable via
`enabled: false`); exporter autowire fallback applies only when
`pipeline.system_nodes` is not provided.

## 3. Compatibility path (`runtime.tracing`)

`runtime.tracing` remains a compatibility path for CLI toggles and legacy
single-sink flows.

Compatibility semantics:

- if `runtime.observability.tracing.exporters[]` is configured, tracing observer
  resolves sinks from observability exporters (DI/registry path);
- if exporters are not configured, tracing observer may fall back to
  `runtime.tracing.enabled + runtime.tracing.sink`.

This fallback is compatibility behavior for old single-sink toggles; exporter
configuration remains the primary contract.

---

## 4. CLI overrides

Framework CLI supports:

- `--tracing enable|disable`
- `--trace-path <path>`

Current override behavior writes legacy compatibility keys:

- `runtime.tracing.enabled`
- `runtime.tracing.sink.name=trace_jsonl`
- `runtime.tracing.sink.settings.path=<trace-path>`

When `--trace-path` is used, CLI also ensures adapter settings include
`adapters.trace_jsonl.settings.path`.

---

## 5. Runtime wiring

At runtime the framework:

1. validates `runtime.observability` tracing/logging/exporter sections (and
   optional pipeline section when provided);
2. materializes exporter adapters via `AdapterRegistry` from
   `runtime.observability.<channel>.exporters[]`;
3. builds execution observers from discovery;
4. binds observer fanout through `FanoutObservabilityService`
   (`ObservabilityPipelineService` contract);
5. materializes observability system dispatch nodes from explicit pipeline config
   or from exporter-driven defaults (at minimum `system.obs.trace_dispatch` when
   tracing exporters are enabled);
6. **runner-dispatch trace emission** (non-blocking hot path — see Section 9):
   when tracing dispatch is enabled, `TracingObserver.after_node()`
   emits `TraceDispatchEvent`; runner routes it to `system.obs.trace_dispatch`, which
   calls `publish_trace_async(...)` on `ObservabilityPipelineService` when running under
   `AsyncRunner` (falls back to `publish_trace(...)` on sync rail); sink/exporter callbacks
   execute without blocking business node callback itself.

No runtime hot-path sink factory wiring is used.

Implementation references:

- Runtime wiring entry: `src/stream_kernel/app/runtime.py`
- Runtime artifact builder: `src/stream_kernel/execution/orchestration/builder.py`
- Execution engine: `src/stream_kernel/execution/runtime/runner.py`
- Tracing observer: `src/stream_kernel/observability/observers/tracing.py`
- Observability adapter registration: `src/stream_kernel/observability/adapters/tracing.py`
- Exporter adapters (with `@adapter` factories): `src/stream_kernel/adapters/trace_sinks.py`
- Platform observability service: `src/stream_kernel/platform/services/observability.py`
- Platform port: `TraceSinkPort` in `src/stream_kernel/adapters/contracts.py`
- System nodes: `src/stream_kernel/execution/orchestration/observability_system_nodes.py`

---

## 6. Tracing scope rules

Tracing is attached at runner execution lifecycle boundaries:

- adapter executed as a DAG node target -> traced as a normal node span;
- adapter injected into a node body -> no separate node span by default;
- injected adapter effects are represented inside caller node span unless
  explicit nested events are emitted.

---

## 7. Tests

Representative coverage:

- `tests/stream_kernel/app/test_tracing_runtime.py`
- `tests/adapters/test_trace_sinks.py`
- `tests/stream_kernel/adapters/test_observability_adapters.py`
- `tests/stream_kernel/observability/test_tracing_observer.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/platform/services/test_observability_pipeline_service.py`

Case matrix (short):

- `TRC-ADP-01`: adapter as graph node -> adapter/node spans present.
- `TRC-ADP-02`: adapter injected into node -> only node span.
- `TRC-ADP-03`: tracing disabled -> no sink writes.
- `TRC-NET-01`: ingress boundary event present.
- `TRC-NET-02`: egress boundary event present.

---

## 8. Stream model

Tracing is one observability stream among:

- tracing (causal path)
- logging (operator events)
- telemetry (numeric runtime signals)
- monitoring (alert-oriented aggregations)

Streams are separable and can be routed to different backends without business
node changes.

---

## 9. Non-blocking trace emission (routing-native path)

**Problem addressed:** prior to this model, `TracingObserver.after_node()` called
`sink.emit(record)` directly and synchronously inside the runner hot path. Every
business record execution included the full OTLP/file I/O cost.

**Current model (pipeline dispatch enabled):**

```
Runner executes business node
  └─ TracingObserver.after_node()
       └─ returns TraceDispatchEvent(record)
              ↑ no sink I/O inside business callback

Runner routes TraceDispatchEvent
  └─ system.obs.trace_dispatch node
       └─ ObservabilityPipelineService.publish_trace_async(record) on async rail
            └─ trace sink/exporter callbacks
```

**Key properties:**

- Runner hot path pays only dispatch/routing cost, not exporter I/O.
- `TracingObserver` excludes reserved `system.obs.*` node names.
- Runner also enforces a centralized guard: `system.obs.*` execution skips
  observability callbacks (`before_node`/`after_node`/`on_node_error`) in both
  sync and async paths.
- Graceful stop (`drain_on_stop=True`) drains pending service messages before exit.
- Async adapters (`execution_mode="async"` in `@adapter` metadata) are awaited
  natively by `AsyncRunner` — no daemon threads, no per-call event loop creation.
- `_run_async_blocking()` is retained only in `close()`/`flush()` shutdown paths
  (one-time blocking at process end is acceptable).
- runner metadata may include `__runner_gap_ms` (same-trace inter-hit hint from runner).
  Effective exported `runner_gap_ms` is computed from previous `TraceRecord.t_exit`
  to current `TraceRecord.t_enter` when previous local trace step exists; otherwise
  runtime falls back to `__runner_gap_ms`.
- jsonl exporter also duplicates route markers at top-level for grep-friendly diagnostics:
  - `process_group`
  - `handoff_from`
  - `route_hop`
  - `runner_gap_ms`
- OTLP payload packing groups spans by `service.name` into shared `resourceSpans`
  (instead of one resource block per span), reducing large-run serialization overhead.

### 9.1 TraceSinkPort (platform port)

`TraceSinkPort` is declared as a `@runtime_checkable` Protocol in
`src/stream_kernel/adapters/contracts.py`:

```python
class TraceSinkPort(Protocol):
    def emit(self, record: object) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

All trace sink adapter factories are annotated with `@adapter(execution_mode=...)`:

| Factory | `execution_mode` |
|---------|-----------------|
| `trace_jsonl_adapter` | `"sync"` |
| `trace_stdout_adapter` | `"sync"` |
| `trace_otel_otlp_adapter` | `"sync"` |
| `trace_otel_otlp_async_adapter` | `"async"` |
| `trace_opentracing_bridge_adapter` | `"sync"` |

`execution_mode="async"` on a trace adapter causes `plan_pools()` to assign the
injecting node (`system.obs.trace_sink`) to the `AsyncRunner` pool, enabling
`emit_async()` to be awaited directly on the running event loop.

### 9.2 Async emit path (emit_async)

`OTelOtlpTraceSink` exposes `async def emit_async(record)`. When
`TraceSinkNode` detects `emit_async` on the sink:

- it returns a coroutine from `__call__()` instead of calling `emit()` directly;
- `AsyncRunner._coerce_node_outputs` awaits the coroutine on the running loop;
- no new event loop or daemon thread is spawned.

`_flush_batch_async()` dispatches to the native async HTTP client:

- `httpx` async mode → `await client.post(...)` (native httpx async)
- `aiohttp` → `async with session.post(...) as response:` (native aiohttp)
- sync backends (urllib, requests, urllib3, grpcio, otel_sdk) →
  `await asyncio.to_thread(self._post_http, batch)` (off-loop, no event-loop block)
- `transport.batch.flush_interval_ms` accepts `0` to disable timer-driven flush
  (count-driven flush only).
- Loop-bound async clients/sessions are recreated on cross-loop shutdown/fallback
  paths (`httpx.AsyncClient`, `aiohttp.ClientSession`) to prevent deadlocks.

### 9.3 SyncRunner graceful stop (Phase D)

`SyncRunner` mirrors `AsyncRunner` for graceful stop:

| Field / Method | Default | Purpose |
|----------------|---------|---------|
| `drain_on_stop: bool` | `True` | when `True`, empties queue before honouring stop |
| `_stop_requested: bool` | `False` | internal flag; set by `request_stop()` |
| `request_stop() -> None` | — | thread-safe graceful stop signal |

Stop-check fires at the top of the `while True` loop; if `drain_on_stop=True` and
the queue still has items (including pending `TraceRecord` envelopes), the runner
continues until the queue is empty before exiting.

### 9.4 Additional test coverage (Phases D–E)

- `tests/stream_kernel/execution/runtime/test_sync_runner_stop.py` — graceful stop
- `tests/stream_kernel/execution/runtime/test_phase_e_async_emit.py` — async emit path
