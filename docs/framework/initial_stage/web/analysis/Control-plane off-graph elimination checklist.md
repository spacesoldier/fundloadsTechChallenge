# Control-plane off-graph elimination checklist

## Goal

Eliminate remaining off-graph execution paths in `root` and `leaf`, leaving only:

1. one minimal external seed input into runner queue
2. process/lifecycle primitives that cannot be represented as graph events

Everything else should move to `@node -> @service -> @store/@adapter`.

## Baseline (current)

### Root: remaining off-graph areas

- Runtime graph/DI assembly in builder (`discover/preflight/build_scenario`).
- Root runner loop orchestration wrapper (`RootRunnerLoopOrchestrationService`).
- Lifecycle start/ready/stop wrapper (`execute_with_runtime_lifecycle`).
- Stop ACK wait loops and direct IPC stop send path.
- Scheduler ticker thread (tick production outside graph queue flow).

### Leaf: remaining off-graph areas

- Process entry orchestration (`leaf_worker_process_entry`) and manual runner boot pulse enqueue.
- Child runtime bootstrap assembling DI/scenario in `DefaultLeafRuntimeBootstrapService`.
- Step assembly mutating consumer registry in `DefaultLeafRuntimeStepAssemblyService`.
- Lane ingress polling fallback order implemented in service (control->data->trace->log->metric).

## Sequential execution plan

### Phase 1. Root startup boundary cleanup

- [x] Move root runtime bootstrap side-effects (`configure_*`, route preload, ingress settings) behind graph commands/events.
- [x] Keep `prepare_process_supervisor_root_runtime` only for minimal lifecycle primitive wiring (or remove if fully graph-covered).
- [x] Add tests for root startup ordering: `init -> bindings bootstrap -> bootstrap dispatch -> discovery/config`.

Done criteria:

- No mutable startup behavior in root pre-run wrapper except lifecycle primitive binding.

Status:

- Completed on 2026-03-12.
- Root pre-run bootstrap wrapper removed from active execution path.
- Root prepare trigger now executes in `system.cp.bootstrap_dispatch` via `ControlPlaneInitEvent.discovery`.

### Phase 2. Root loop cleanup

- [x] Remove startup/deferred split policy from `RootRunnerLoopOrchestrationService`.
- [x] Keep input enqueue generic; startup semantics must be graph-driven.
- [x] Preserve only thin `run_until_stopped` invocation boundary.

Done criteria:

- Root loop service does not inspect control-plane payload types.

Status:

- Completed on 2026-03-12.
- Root loop now enqueues all initial inputs in root control-plane mode and delegates startup semantics to graph nodes.
- `startup/deferred` split removed from runtime loop orchestration.
- Verification:
  - `tests/stream_kernel/execution/orchestration/runtime` (green)
  - control-plane e2e handshake/pipeline/scale tests (green)

### Phase 3. Root shutdown path unification

- [x] Move stop orchestration to graph events and system nodes.
- [x] Remove blocking ACK wait loops from stop service path.
- [x] Keep one control-plane stop route (no duplicate stop drivers).

Done criteria:

- Root stop completion is driven by graph state/quorum events.

Status:

- Completed on 2026-03-12.
- Root shutdown path is graph-driven: `system.cp.root_stop` emits `ControlPlaneRootLeafStopRequestEvent`,
  and `system.cp.root_stop_dispatch` sends typed stop commands over control IPC lane.
- Off-graph root stop/shutdown wrapper services removed, together with legacy tests tied to that path.

### Phase 4. Root ingress topology fix (worker+lane)

- [x] Build root IPC ingress source nodes per `worker_id + lane` (no lane-only source).
- [x] Remove lane-mixed polling path from root ingress service.
- [x] Keep transport routing deterministic per lane without mixing streams.

Done criteria:

- No cross-worker/cross-lane multiplexing inside one source node.

Status:

- Completed on 2026-03-12.
- Root ingress source registration now uses static `worker_id + lane` node names:
  `source:system.cp.root_leaf_ingress:{worker_id}:{lane}`.
- Root ingress source node polls only `poll_next_leaf_ingress_for_worker_lane*` and no longer falls back
  to lane-only or mixed worker polling paths.
- Root ingress service no longer exposes lane-mixed poll methods.

### Phase 5. Leaf entry cleanup

- [x] Remove manual bootstrap pulse enqueue helper from process entry orchestration.
- [x] Introduce graph-native initialization event path for leaf startup.
- [x] Keep process entry only for process primitive concerns (env/process bootstrap).

Done criteria:

- Leaf startup business/control events are emitted by graph nodes only.

Status:

- Completed on 2026-03-12.
- Leaf process entry now enqueues `ControlPlaneInitEvent` to
  `system.cp.consumer_registry_bindings_bootstrap`; no direct pulse enqueue to
  `system.cp.leaf_bootstrap` remains.
- Startup handshake in leaf goes through graph chain only:
  `init -> consumer_registry_bindings_bootstrap -> bootstrap_dispatch -> leaf_bootstrap`.
- Legacy off-graph helper for direct startup event fabrication removed from runtime path.

### Phase 6. Leaf runtime bootstrap reduction

- [x] Minimize `DefaultLeafRuntimeBootstrapService` to process bootstrap essentials.
- [x] Move dynamic registration and startup behavior to control-plane nodes/services.
- [x] Reduce direct builder-coupling in child bootstrap.

Done criteria:

- Leaf bootstrap service is not a second orchestration center.

Status:

- Completed on 2026-03-13.
- Completed:
  - `DefaultLeafRuntimeBootstrapService` stays orchestration-only:
    `validate -> assembly_service -> step_assembly_service -> profile/export/runtime handles`.
  - Heavy bootstrap/build logic remains isolated in dedicated
    `DefaultLeafRuntimeBootstrapAssemblyService`.
  - Direct builder object coupling removed from child bootstrap contract:
    `LeafRuntimeBootstrapAssemblyResult` no longer exposes `execution_builder` / `consumer_registry`;
    `LeafRuntimeStepAssemblyService` no longer requires them.
  - Dynamic startup consumer registration moved to control-plane startup bindings flow:
    leaf step assembly now seeds `ControlPlaneStartupConsumerBindingsService` via
    `seed_startup_consumer_bindings(...)`, and runtime registry mutations happen in graph through
    `system.cp.consumer_registry_bindings_bootstrap` -> `system.cp.consumer_registry_bindings_apply`.
  - Transitional compatibility preload kept for boundary-only execution helpers:
    leaf step assembly preloads the same bindings via `ControlPlaneDynamicConsumerRoutingService`
    so isolated boundary runs (without full control-plane init chain) keep deterministic sink routing.
  - Added ingress/egress planning service boundary:
    `LeafRuntimeIngressEgressPlanningService` (default implementation wraps source/sink synthesis).
  - Startup input order hardened for supervisor mode:
    `ControlPlaneInitEvent` now precedes source bootstrap inputs.
  - Verification:
    - leaf startup contracts:
      `tests/stream_kernel/execution/orchestration/lifecycle/leaf/startup/test_runtime_bootstrap_service_contract.py`
      `tests/stream_kernel/execution/orchestration/lifecycle/leaf/startup/test_runtime_step_assembly_service_contract.py`
    - startup assembly contract:
      `tests/stream_kernel/execution/orchestration/runtime/test_startup_scenario_assembly.py`
    - builder init-event smoke:
      `tests/stream_kernel/execution/orchestration/test_builder.py::{test_build_runtime_artifacts_orders_root_bootstrap_after_consumer_bindings,test_build_runtime_artifacts_injects_root_init_event_for_process_supervisor_mode,test_build_runtime_artifacts_injects_leaf_init_event_for_worker_process}`
    - control-plane e2e gate:
      handshake + cross-process pipeline + startup scale tests (8 passed).

### Phase 7. Scheduler normalization

- [x] Replace ticker thread loop with graph-owned scheduling tick source model.
- [ ] Keep scheduler store/service, but tick production should be lifecycle-consistent and explicit.
- [ ] Ensure no hidden background loops outside graph control contract.

Done criteria:

- Scheduler ticks are observable/control-plane-managed and testable as graph flow.

Status:

- In progress since 2026-03-13.
- Completed:
  - Added graph node `system.scheduler.timer` and DI service `PlatformSchedulerTimerService`
    with KV-backed timer job store.
  - `PlatformSchedulerUpsertCommand` / `PlatformSchedulerCancelCommand` now route to both
    `system.scheduler.command` and `system.scheduler.timer` in root and leaf plans.
  - Root/leaf control-plane source initialize path switched from `ticker.ensure_started()` to
    explicit timer service `apply_command(...)`.
  - Added timer coverage in scheduler node tests and kept control-plane e2e gate green.

### Phase 8. Final hardening and deletion pass

- [ ] Delete dead compatibility helpers and legacy aliases.
- [ ] Remove duplicate fallback branches once graph path is authoritative.
- [ ] Update docs/protocol notes to reflect single-path control-plane.

Done criteria:

- No duplicate control paths (`off-graph` vs `on-graph`) for same responsibility.

### Phase 9. Optional transport uplift (deferred)

- [ ] (Optional) Replace per-source sync IPC `recv` path with shared async-friendly multiplexer
  (`multiprocessing.connection.wait(...)` for all lane endpoints in process).
- [ ] (Optional) Keep one ingress fan-in queue with explicit source metadata
  (`run_id`, `worker_id`, `group`, `lane`, `seq_no`, `source_channel_id`).
- [ ] (Optional) Preserve graph-only consumption: source nodes read fan-in queue only; no direct
  transport polling in nodes.

Done criteria:

- No per-channel thread model.
- IPC ingress remains non-blocking for runner event loop under high lane/process count.
- Transport concerns remain optional and isolated from control-plane logic changes.

## Test gate for every phase

For each completed phase:

1. targeted unit tests for touched services/nodes
2. control-plane e2e handshake test
3. cross-process pipeline e2e test
4. startup scale e2e test

No phase is marked complete without all four checks green.
