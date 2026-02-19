# Observability Phase L — runner dispatch + recursion guard closure (TDD)

## Context

After Phases A-K, two runtime invariants must be enforced end-to-end:

1. observability service traffic (trace/log/metric/monitor dispatch messages) must be delivered on execution rails by runner routing, not by ad-hoc side effects;
2. system observability work (`system.obs.*`) must never generate new tracing spans, otherwise the platform can enter recursive self-observation.

Date: 2026-02-17

## Status

- [x] Step A completed (RED tests for runner-level service-output routing).
- [x] Step B completed (runner routes outputs returned by observability callbacks).
- [x] Step C completed (RED tests for `system.obs.*` recursion guard).
- [x] Step D completed (reserved-prefix guard implemented in tracing observer).
- [x] Step E completed (trace dispatch mode via `TraceDispatchEvent` + factory toggle).
- [x] Step F completed (focused regression suites executed).

## Target invariants

- `OBS-L-01`: if an observer emits service outputs from `after_node`/`on_node_error`, runner routes them through `RoutingService` and `QueuePort` exactly like business outputs.
- `OBS-L-02`: tracing observer never records spans for nodes whose names are `system.obs.*` (qualified names included).
- `OBS-L-03`: trace record delivery can be switched to runner-dispatch mode (record -> `TraceDispatchEvent`) without direct sink I/O in business node callback.
- `OBS-L-04`: no regressions for existing sink emission mode and graceful `on_run_end` lifecycle.

## TDD steps

### Step A — RED: runner-level service-output routing

- Add tests proving runner consumes outputs returned by observability callbacks and enqueues routed envelopes.
- Cover at least sync path (`SyncRunner`); keep async path contract aligned in implementation.

### Step B — GREEN: wire runner dispatch path

- Extend runner execution loop to process outputs returned from:
  - `observability.after_node(...)`
  - `observability.on_node_error(...)`
- Reuse existing routing semantics (`RoutingService.route` + queue push with trace/reply/span continuity).

### Step C — RED: recursion guard

- Add tests proving `TracingObserver.before_node(...)` skips:
  - `system.obs.trace_dispatch`
  - `system.obs.trace_sink`
  - qualified names like `system.obs.metric_dispatch:obs.async`

### Step D — GREEN: strict system-node exclusion

- Implement reserved-prefix exclusion (`system.obs.*`) in tracing observer.
- Keep explicit exclusion set support for future extension.

### Step E — RED/GREEN: runner-dispatch trace emission mode

- Add tests proving tracing observer can emit `TraceDispatchEvent` instead of direct sink call.
- Implement opt-in mode in observer factory when pipeline dispatch nodes are configured.
- Preserve legacy sink emission mode for non-pipeline configs.

### Step F — Regression gate

- Run focused suites:
  - `tests/stream_kernel/execution/runtime/test_runner_observers.py`
  - `tests/stream_kernel/observability/test_tracing_observer.py`
  - `tests/stream_kernel/observability/test_tracing_observer_factory.py`
  - `tests/stream_kernel/execution/orchestration/test_builder.py -k observability`

## Acceptance criteria

- service dispatch for observability is runner-driven and test-covered;
- `system.obs.*` execution is trace-silent by contract;
- no infinite trace amplification path remains in sync or async runner execution;
- existing parity tests remain green.
