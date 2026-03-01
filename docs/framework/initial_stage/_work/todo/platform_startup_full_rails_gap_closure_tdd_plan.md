# Platform startup full-rails gap closure (TDD plan)

## Goal

Close remaining startup gaps and reach a fully platform-rail launch model:

- startup orchestration is message-driven (`@node -> @service -> @adapter`);
- services use injected platform ports/stores only;
- no hidden builder/bootstrap hardcode for discovery/config/launch planning;
- root and leaf startup are deterministic and non-blocking on hot path.

Related docs:

- [Platform discovery runtime stream scenario](../../web/analysis/Platform%20discovery%20runtime%20stream%20scenario.md)
- [Root async bootstrap with discovery and config streams](../../web/analysis/Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)
- [Control-plane bootstrap root-leaf sequences](../../web/analysis/Control-plane%20bootstrap%20root-leaf%20sequences.md)
- [IPC transport and worker lifecycle services](../../web/analysis/IPC%20transport%20and%20worker%20lifecycle%20services.md)
- [Leaf full platform-rails + async runtime gap closure](leaf_full_platform_rails_async_runtime_tdd_plan.md)

## Definition of done (full rails)

1. Root startup path is `ControlPlaneRootPulse -> discovery stream -> config stream -> startup barrier -> DAG assembly -> launch plan -> spawn` with no builder-time fallback path.
2. Leaf apply path is `leaf_config_card -> leaf activation service -> typed ack`, where activation is based on platform discovery/config stores and not on no-op bootstrap adapters.
3. `builder.py` prepares only minimal runtime kernel wiring and does not own startup decisions that belong to control-plane nodes/services.
4. No service-side source-of-truth dicts for runtime startup state; state is in injected platform stores.
5. Runtime loop remains non-blocking on hot path; blocking waits are isolated and bounded by explicit timeout contracts.

## Current gap map

### GAP-01: missing DI bindings for discovery source adapters in runtime startup slice

Observed failure:

- `Missing binding for service<ControlPlaneDiscoverySourceAdapter>#platform_discovery_source_adapter`
- fails in runtime e2e startup-barrier path during `build_runtime_artifacts`.

Where:

- `src/stream_kernel/platform/services/runtime/control_plane_discovery_stream.py`
- `src/stream_kernel/execution/orchestration/builder.py`

Impact:

- root startup cannot reliably instantiate discovery stream service through DI in all runtime entry paths.

### GAP-02: root planning still contains hard fallback constructors

Where:

- `src/stream_kernel/execution/orchestration/control_plane/root/system_nodes.py`
  - `resolve_discovery_stream`
  - `resolve_config_stream`
  - `resolve_discovery_service`
  - `resolve_state_service`
  - `resolve_startup_barrier`
  - `resolve_dag_assembly_service`

Impact:

- node wiring can silently bypass DI graph and instantiate ad-hoc in-memory services;
- this breaks strict platform contract and complicates determinism/diagnostics.

### GAP-03: launch planning still falls back to direct runtime map

Where:

- `src/stream_kernel/platform/services/runtime/control_plane_launch_plan.py`

Impact:

- execution-group plan may be computed from `runtime.platform.process_groups` directly;
- startup stream stores are not the single source of truth.

### GAP-04: config stream records are not fully applied through dedicated apply rails

Current state:

- config records are streamed and persisted;
- launch plan consumes execution-group records;
- node/system/observability records are not fully applied by explicit apply services/nodes with typed side effects.

Impact:

- startup semantics remain partially declarative, partially implicit.

### GAP-05: leaf config activation still depends on legacy bootstrapper adapter contract

Where:

- `src/stream_kernel/platform/services/runtime/control_plane_bootstrapper.py`
- `src/stream_kernel/execution/orchestration/lifecycle/leaf/runtime/runtime_activation_service.py`

Current issue:

- default bootstrap discovery adapter is no-op;
- leaf apply can “succeed” without real platform discovery-backed activation inputs.

### GAP-06: builder still owns too much startup orchestration

Where:

- `src/stream_kernel/execution/orchestration/builder.py`

Current issue:

- builder preloads modules, runs preflight scenario assembly, and composes startup steps with fallback-heavy wiring;
- startup ownership remains split between builder and control-plane nodes.

### GAP-07: blocking wait remains in startup handshake path

Where:

- `src/stream_kernel/execution/orchestration/control_plane/root/startup_handshake_service.py`

Current issue:

- `time.sleep(...)` loop is used for group startup ack wait.

Impact:

- bounded but still blocking; increases startup latency and contention risk.

## Delivery plan (TDD phases)

## Phase A — close DI discovery binding gap (GAP-01)

RED tests:

1. Runtime artifacts build in process-supervisor mode resolves both qualified discovery source adapters from DI.
2. Startup barrier runtime e2e passes without manual/fallback adapter construction.

GREEN code:

- ensure qualified bindings for:
  - `ControlPlaneDiscoverySourceAdapter#platform_discovery_source_adapter`
  - `ControlPlaneDiscoverySourceAdapter#project_discovery_source_adapter`
- guarantee bindings are present in both root and leaf runtime artifact build paths.

## Phase B — remove root startup fallback constructors (GAP-02)

RED tests:

1. `build_control_plane_system_plan` fails deterministically if required services are missing (no in-memory fallback).
2. Root system nodes use DI-resolved services only.

GREEN code:

- delete fallback `resolve_*` constructor paths from root system node module;
- keep only explicit DI resolution and deterministic error surface.

## Phase C — make startup stores the only launch-plan source (GAP-03)

RED tests:

1. Launch-plan service builds plan from startup config/discovery stores only.
2. Direct runtime-map fallback path is rejected in process-supervisor startup mode.

GREEN code:

- remove direct `runtime.platform.process_groups` fallback from launch-plan service in startup flow;
- keep runtime-map fallback only for explicit non-supervisor modes if still required by contract.

## Phase D — add config apply rails for non-group records (GAP-04)

RED tests:

1. Node config records are consumed by typed apply node/service and persisted in dedicated startup store section.
2. Observability/system runtime config records are applied via typed service contracts before DAG assembly.
3. DAG assembly/startup barrier depends on apply completion events, not just stream completion.

GREEN code:

- add `system.cp.config_apply_*` nodes/services for:
  - system runtime config
  - observability config
  - node config
- add apply-completed events and barrier integration.

## Phase E — replace legacy leaf bootstrapper dependency in activation path (GAP-05)

RED tests:

1. `LeafRuntimeActivationService.apply_config` resolves activation inputs from platform discovery/config stores.
2. No-op bootstrap discovery adapter is not used in leaf config apply path.
3. Rejected ack is deterministic on missing required node/service metadata.

GREEN code:

- remove `ControlPlaneBootstrapperService` dependency from leaf runtime activation;
- use dedicated platform discovery/config access services through DI.

## Phase F — shrink builder startup ownership to minimal kernel (GAP-06)

RED tests:

1. Builder only prepares minimal kernel:
  - DI registry wiring
  - minimal startup system nodes
  - root/leaf pulse injection
2. Discovery/config/plan/spawn decisions are produced by runtime events, not builder branching.

GREEN code:

- move remaining startup decision logic from builder to control-plane/lifecycle services;
- keep builder as runtime assembly shell and orchestration launcher only.

## Phase G — non-blocking startup handshake path (GAP-07)

RED tests:

1. Startup handshake path does not use blocking sleep loop in root hot path.
2. Root runner loop timeout/poll policy governs handshake progression deterministically.

GREEN code:

- replace blocking wait strategy with runner-loop/event-driven drain strategy;
- keep explicit timeout behavior and typed timeout errors.

## Regression gates (must stay green every phase)

1. Control-plane root node suites:
   - discovery stream nodes
   - config stream node
   - startup barrier node
   - init plan node
2. Lifecycle root/leaf startup service suites.
3. Runtime startup barrier e2e suite.
4. Real IPC control-plane e2e suite (handshake/config/boundary/stop).

## Suggested execution order

1. Phase A first (unblocks failing startup barrier e2e).
2. Phase B + C next (remove hidden fallback semantics).
3. Phase D + E (complete config/leaf apply platformization).
4. Phase F + G (builder minimization and non-blocking startup hardening).

## Execution status

- [x] Phase A implemented in runtime wiring: qualified DI bindings for discovery source adapters are registered before startup node composition.
- [x] Phase B implemented in control-plane planning: root startup plan now resolves required services from DI only (no ad-hoc in-memory constructors).
- [x] Phase C implemented in launch-plan service: `process_supervisor` startup path no longer falls back to `runtime.platform.process_groups` when startup stores are empty.
- [x] Phase D implemented in control-plane startup flow: system/observability/node config records now pass through typed apply nodes/services and startup barrier opens on config-apply completion event.
- [x] Phase E implemented in leaf activation path: leaf config apply no longer uses bootstrapper subset discovery and validates activation inputs via discovery/config stores.
- [x] Phase F implemented: startup branching and startup scenario/input composition are moved from `builder.py` into control-plane/runtime orchestration modules.
- [x] Phase G implemented in startup path: lifecycle group startup wait no longer performs blocking wait/sleep loop and now progresses from ack events.

Phase F + G closure notes:

- [x] Startup-mode branching for pulse emission and business-step inclusion moved out of `builder.py` into control-plane planning helpers (`control_plane_bootstrap_inputs`, `should_include_business_steps`).
- [x] Startup scenario/input composition moved out of `builder.py` into orchestration runtime assembly module (`assemble_runtime_startup_scenario`).
- [x] Root process-supervisor preparation hook moved out of `builder.py` into runtime module (`prepare_process_supervisor_root_runtime`).
- [x] `system.lifecycle.group_startup_wait` now tracks readiness from incoming `ControlPlaneLeafConfigAckEvent` messages via state service, removing blocking `wait + sleep` startup handshake in node hot path.
- [x] Legacy startup-handshake service was removed from control-plane exports and platform discovery wiring; runtime startup path now uses event-driven lifecycle nodes only.
- [x] Legacy startup-handshake implementation and dedicated unit test were removed; real-spawn IPC e2e flows now perform startup ack wait through ingress/state-driven helper logic (no startup-handshake service dependency).

## Notes

- Keep docs -> tests -> code strict in each phase.
- Do not add shims/compat layers for removed startup fallback behavior.
- If a phase breaks old tests tied to removed hardcode semantics, replace those tests with platform-contract tests in matching module structure.

## Runtime readiness snapshot (2026-02-28)

Validation summary after phase closures:

- `control_plane` real-spawn IPC e2e suites: green.
- `runtime` startup barrier e2e suite: green.
- `execution/runtime` async runner suite: green.
- Full application smoke (`python -m fund_load --config src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml`): **not green** (startup timeout; only `spawn_requested` lifecycle events observed, no `group_startup_ready`).

Operational readiness status:

- Platform-rail startup phases are closed at test-contract level.
- End-to-end runtime readiness for full app launch is **partial** and requires a dedicated startup handshake smoke test with real leaf worker entrypoint (not worker stubs).
