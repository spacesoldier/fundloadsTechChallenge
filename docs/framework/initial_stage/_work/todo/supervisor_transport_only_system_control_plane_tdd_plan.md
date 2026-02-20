# Supervisor Transport-Only and System Control Plane (TDD plan)

## Goal

Move multiprocess runtime to a strict role split:

- supervisor: routing + lifecycle orchestration only;
- worker groups: business execution only;
- system control plane: observability/logging/monitoring system nodes + services + adapters.

Primary objective is to remove blocking observability side effects from supervisor hot path and keep transport replaceable (IPC today, other transport later) without changing orchestration semantics.

---

## Why now

Observed in experiments on February 20, 2026:

- run may stall right after `worker_ready` when only `otel_otlp_logical` is enabled;
- full tracing-off run completes in ~2 seconds, but tracing-on run stalls/hangs;
- stack traces show blocking `multiprocessing.Connection.send()` on trace-forward paths.

Current blocking points (must be eliminated from hot path):

- worker -> supervisor trace bootstrap message send;
- supervisor -> `system.observability` trace forwarding send.

---

## Scope

In scope:

- supervisor role hardening (transport-only + lifecycle coordination);
- control-plane execution on platform rails (`system.obs.*` nodes/services/adapters);
- non-blocking dispatch contracts with deterministic backpressure policy;
- transport abstraction boundaries for future non-IPC backend;
- documentation + tests-first delivery.

Out of scope:

- cross-host distributed runtime;
- business DAG redesign;
- replacing OTLP protocol itself.

---

## Target architecture

## 1) Roles

- `supervisor`: process-group router, boundary dispatcher, readiness/shutdown coordinator, minimal console lifecycle output.
- `execution.*`: business nodes and business adapters only.
- `system.observability`: trace/log/metric/monitor dispatch nodes and all exporter ownership.

## 2) Channel model

- separate control-plane dispatch path for `system.obs.*` traffic from business payload path;
- explicit backpressure per channel with deterministic policy (`drop_newest | drop_oldest | block_with_timeout`);
- no synchronous `send()` from supervisor callback paths where traffic volume can burst.

## 3) Ownership model

- observability owner process is single writer for file/OTLP/Prometheus exporters;
- supervisor does not instantiate observability exporters/endpoints in dedicated mode;
- supervisor keeps only console lifecycle sink for terminal visibility.

---

## Contract updates (planned)

- keep external config shape stable where possible;
- formalize supervisor role constraints in docs and validator/runtime contracts;
- introduce explicit transport/control-plane service interfaces:
  - `ControlPlaneDispatchPort` (logical contract),
  - `BusinessDispatchPort` (logical contract),
  - both mapped by transport adapters.

---

## Phases

## Phase A — Contract freeze and docs alignment

Status (2026-02-20): completed.

RED:

- add failing tests that assert supervisor does not own tracing/logging/monitoring exporters when dedicated observability process is enabled;
- add failing tests for role invariants (`supervisor`, `worker`, `observability_worker`).

GREEN:

- freeze contract in docs (`Tracing runtime`, `Execution runtime and routing integration`, this plan);
- normalize wording: supervisor is not exporter owner in dedicated mode.

REFACTOR:

- remove contradictory ownership statements in docs.

Exit criteria:

- docs and tests describe one unambiguous ownership model.

## Phase B — Non-blocking trace forward path

Status (2026-02-20): completed (supervisor hot-path side).

RED:

- reproduce and codify hang case with logical OTLP-only config;
- add tests proving no blocking `send()` in supervisor hot path for trace forwarding.

GREEN:

- replace direct supervisor trace forwarding `send()` with queued non-blocking dispatch on control-plane rails;
- enforce deterministic backpressure policy + counters.

REFACTOR:

- centralize dispatch/drop diagnostics.

Exit criteria:

- no startup stall in logical-only tracing scenario.

Residual note:

- worker -> supervisor `worker_trace` control messages still use blocking pipe send path;
  this remaining pressure point is handled in subsequent phases.

## Phase C — System-node lifecycle telemetry completeness

Status (2026-02-20): completed.

RED:

- tests for message lifecycle coverage:
  - worker receive,
  - worker emit,
  - supervisor receive,
  - supervisor dispatch.

GREEN:

- emit lifecycle telemetry as system-node messages, not inline supervisor export calls;
- keep trace-view slicing logic in observability owner.

REFACTOR:

- reduce duplicated span construction logic.

Exit criteria:

- topology/logical views stay complete and consistent under load.

## Phase D — Channel separation and transport abstraction

Status (2026-02-20): completed (dispatch-port abstraction baseline).

RED:

- tests showing control-plane congestion does not block business dispatch;
- tests for transport-agnostic dispatch services.

GREEN:

- introduce/finish explicit dispatcher abstractions for business vs control traffic;
- map current IPC implementation behind these abstractions.

REFACTOR:

- remove direct transport calls from supervisor orchestration internals.

Exit criteria:

- transport can be swapped with minimal orchestration changes.

Residual note:

- both business/control default ports are still backed by the same IPC pipe transport implementation;
  the abstraction boundary is now explicit, so transport swap can proceed without orchestration rewrites.

## Phase E — Shutdown/drain safety without deadlock

Status (2026-02-20): completed (channel diagnostics + saturated-loop stop safety).

RED:

- tests for graceful stop ordering with explicit pending/dropped accounting per channel;
- tests for no deadlock on stop when observability queue is saturated.

GREEN:

- enforce shutdown sequence:
  1) stop business ingress,
  2) drain business-to-control telemetry,
  3) close observability exporters,
  4) stop remaining groups.

REFACTOR:

- unify stop diagnostics in lifecycle + monitoring snapshots.

Exit criteria:

- deterministic stop with explicit loss accounting.

Residual note:

- channel accounting is currently emitted through the existing `trace_dispatch_diagnostics` snapshot;
  if needed, this can be split into a dedicated `channel_dispatch_diagnostics` event in a follow-up.

## Phase F — Performance gate and migration closure

Status (2026-02-20): completed.

RED:

- regression perf tests vs current baseline:
  - startup latency,
  - total processing time,
  - queue wait metrics.

GREEN:

- tune defaults (queue capacities, flush intervals, backpressure budgets);
- finalize migration notes and operator runbook.

REFACTOR:

- remove dead compatibility branches that keep supervisor-owned export paths in dedicated mode.

Exit criteria:

- no major regression, no known stall class, contracts documented and test-covered.

Closure notes:

- Added committed Phase F perf report with reproducible benchmark command:
  `supervisor_transport_only_phasef_regression_perf_report.md`.
- Added perf-gate test that enforces report presence and mandatory sections.
- Removed dedicated-mode inline trace-forward fallback in supervisor hot path;
  if dispatch loop is unavailable, records are dropped and counted deterministically
  instead of attempting synchronous transport send from supervisor call path.

---

## Suggested test map

- `tests/stream_kernel/platform/services/runtime/test_bootstrap_supervisor_contract.py`
- `tests/stream_kernel/platform/services/runtime/test_bootstrap_supervisor_boundary_delegation.py`
- `tests/stream_kernel/platform/services/runtime/test_process_group_router_service.py`
- `tests/stream_kernel/platform/services/runtime/test_async_dispatch_loop.py`
- `tests/stream_kernel/observability/test_observability_system_node_registry.py`
- `tests/adapters/test_trace_sinks.py`

---

## Notes

- This plan extends and tightens:
  - `observability_dedicated_service_process_tdd_plan.md`
  - `runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md`
  - `platform_api_phaseF_observability_diagnostics_tdd_plan.md`
- It does not change business-domain contracts from challenge docs.
