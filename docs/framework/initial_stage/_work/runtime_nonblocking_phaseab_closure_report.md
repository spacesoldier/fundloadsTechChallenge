# Runtime non-blocking observability — Phase A + B closure report

## Date: 2026-02-15

## Summary

Phase A and Phase B of `runtime_nonblocking_and_graceful_stop_audit_plan.md` are complete.
Test suite: **846 passed, 0 failed** after both phases.

---

## Phase A — TraceSinkPort as platform port

### What changed

| Component | Before | After |
|-----------|--------|-------|
| `TraceSinkLike` | Private `Protocol` inside `observability/observers/tracing.py` | Promoted to `TraceSinkPort` — `@runtime_checkable Protocol` in `adapters/contracts.py` |
| Trace sink factories | No `@adapter` metadata | 5 `@adapter`-decorated factory functions with `execution_mode` declared |
| `trace_otel_otlp_async_adapter` | Did not exist | New factory; `execution_mode="async"` triggers async pool assignment via `plan_pools()` |
| Lambda mocks in `test_framework_run.py` | `lambda _instances, _bindings: ...` | Fixed to `lambda _instances, _bindings, **_kw: ...` (regression from Phase J.1) |

### Adapter execution modes

| Adapter | `execution_mode` | Rationale |
|---------|-----------------|-----------|
| `trace_jsonl_adapter` | `sync` | File I/O via blocking write; isolated in own runner turn after Phase C |
| `trace_stdout_adapter` | `sync` | stdout write; same isolation after Phase C |
| `trace_otel_otlp_adapter` | `sync` | urllib / requests / httpx-sync / urllib3 / grpcio / otel_sdk backends |
| `trace_otel_otlp_async_adapter` | `async` | httpx-async / aiohttp backends; awaited directly in Phase E |
| `trace_opentracing_bridge_adapter` | `sync` | Callback bridge; no I/O contract |

---

## Phase B — @node / @service rails for observability

### What changed

| Component | Before | After |
|-----------|--------|-------|
| `ObservabilityDispatchNode` | Single generic `@dataclass`, no `@node`, manual `StepSpec` insertion | Replaced by 4 concrete `@node`-decorated classes (see below) |
| `TraceSinkNode` | Did not exist | New `@node(name="system.obs.trace_sink")` `@dataclass` with `inject.stream(TraceSinkPort)` |
| `FanoutObservabilityService` | Plain `@dataclass`, not DI-discoverable | `@service(name="fanout_observability_service")` added |
| `ReplyAwareObservabilityService` | Plain `@dataclass`, `inner` required, not DI-discoverable | `@service(name="reply_aware_observability_service")` added; `inner` defaults to `None` (degrades to NoOp via `_inner()`) |
| `TracingObserver` | No recursion guard | `excluded_node_names: frozenset[str]` param; `before_node()` returns `None` immediately for excluded nodes |
| `build_observability_system_plan()` | Created `ObservabilityDispatchNode` instances | Creates concrete dispatch node instances via `_KIND_TO_NODE_CLS[kind]` |

### Concrete dispatch node classes

| Class | `@node` name | Event type consumed | Pipeline method |
|-------|-------------|---------------------|-----------------|
| `TraceDispatchNode` | `system.obs.trace_dispatch` | `TraceDispatchEvent` | `publish_trace` |
| `LogDispatchNode` | `system.obs.log_dispatch` | `LogDispatchEvent` | `publish_log` |
| `MetricDispatchNode` | `system.obs.metric_dispatch` | `MetricDispatchEvent` | `publish_metric` |
| `MonitorDispatchNode` | `system.obs.monitor_dispatch` | `MonitorDispatchEvent` | `publish_monitoring` |

Each class carries:
- `pipeline: ObservabilityPipelineService` — resolved pipeline passed at build time
- `qualifier: str | None = None` — forwarded from config
- `_obs_marker: object` (set in `__post_init__`) — `inject.service(ObservabilityPipelineService, qualifier=qualifier)` stored in instance `__dict__` so `plan_pools()` can detect async capability per qualifier

### Recursion guard (B3)

`TracingObserver.before_node()` returns `None` immediately when
`node_name in self._excluded_node_names`. Since `state is None`, the
`after_node()` / `on_node_error()` paths are already no-ops (existing guard).
Builder passes `ObservabilitySystemPlan.system_node_names` to populate this set,
preventing trace-sink-node execution from generating new trace events.

---

## Files modified

| File | Change |
|------|--------|
| `src/stream_kernel/adapters/contracts.py` | Added `TraceSinkPort` `@runtime_checkable Protocol` |
| `src/stream_kernel/adapters/trace_sinks.py` | Added `_otlp_kwargs()` helper + 5 `@adapter` factory functions |
| `src/stream_kernel/execution/orchestration/observability_system_nodes.py` | Added 4 concrete dispatch classes + `TraceSinkNode` + `_KIND_TO_NODE_CLS` mapping; updated `build_observability_system_plan()` |
| `src/stream_kernel/platform/services/observability.py` | `@service` on `FanoutObservabilityService` and `ReplyAwareObservabilityService`; `inner=None` default |
| `src/stream_kernel/observability/observers/tracing.py` | `excluded_node_names` param + guard in `before_node()` |
| `tests/stream_kernel/adapters/test_platform_adapter_execution_modes.py` | 3 new Phase A tests |
| `tests/stream_kernel/app/test_framework_run.py` | Fixed 5 lambda mock signatures |
| `tests/stream_kernel/observability/test_observability_system_node_registry.py` | New Phase B test file (5 tests) |
| `tests/stream_kernel/observability/test_tracing_observer.py` | 2 new Phase B3 tests |

---

## Pending

- Phase C — routing-native trace emission (TracingObserver routes TraceRecord to work queue)
- Phase D — SyncRunner stop signal + multiprocess output-closed ack
- Phase E — retire `_run_async_blocking` from hot path
- Phase F — docs sync
