# Root async bootstrap + discovery/config stream TDD plan

## Scope

Implement startup architecture where root runtime is always async and startup is
fully platform-rail (`@node -> @service -> @adapter`) with a discovery+config
completion barrier.

Reference analysis:

- [Root async bootstrap with discovery and config streams](../../web/analysis/Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)
- [Platform startup full-rails gap closure (TDD plan)](platform_startup_full_rails_gap_closure_tdd_plan.md)

## Rules

1. Docs -> tests -> code.
2. No tests asserting presence of docs/files.
3. No shim/compat layers for removed bootstrap paths.
4. Keep startup events typed and deterministic.

## Phase A — Root async runner policy (done)

Goal:

- root execution path uses async runner only.

Tests:

1. `execute_runtime_artifacts` delegates to async runner.
2. sync-only dependency graph still uses async runner.
3. explicit `runner_profile: sync` does not switch root runner.

Status:

- Completed in current branch (`builder._execute_runner` async-only path).

## Phase B — Discovery rails (`node -> service -> adapter`) [done]

Goal:

- discovery flow grounded in adapter contract, not service-only placeholder.

Tests (red first):

1. discovery adapter is declared via `@adapter` contract.
2. bootstrap service accepts discovery adapter through discovery port contract.
3. root bootstrap node emits `discovery.completed` after streamed items.
4. leaf subset discovery uses same service contract with filtered node set.

Code target:

- `platform/services/runtime/control_plane_bootstrapper.py`
- `execution/orchestration/control_plane/root/system_nodes.py`
- `execution/orchestration/control_plane/leaf/system_nodes.py`

## Phase C — Config stream contracts + YAML adapter [done]

Goal:

- introduce typed startup config records and stream completion event.

Tests:

1. YAML adapter streams records in deterministic section order.
2. invalid section payload emits deterministic validation error.
3. config stream service accumulates typed records and emits completed event.
4. startup store can return normalized records by section.

Code target:

- `platform/services/runtime/*config*` (new module set)
- typed events/contracts in control-plane runtime event module.

## Phase D — Startup barrier [done for root loop gating]

Goal:

- block launch/spawn/business ingress until discovery+config stream complete.

Tests:

1. launch plan node does not run before both completions.
2. spawn dispatch is blocked while barrier is closed.
3. barrier opens once and is idempotent on duplicate completed events.
4. root pulse full chain: root pulse -> discovery/config -> barrier open -> init/plan/spawn.

Current status:

1. `system.cp.root_config_stream` node + config stream service/adapter/store are implemented.
2. Typed config records + `config.completed` event are implemented and covered by tests.
3. Startup barrier service/node are implemented with idempotent open behavior.
4. Root chain `root pulse -> discovery/config -> barrier -> init/plan/spawn` is covered by unit tests.
5. Runtime root loop now waits for startup barrier before replaying boundary outputs and before enqueuing deferred ingress/business inputs.
6. `runtime.platform.runner_loop.startup_barrier_timeout_ms` is supported (with default timeout in code) and covered by tests.
7. Full runtime path (`build_runtime_artifacts -> execute_runtime_artifacts`) is covered by e2e tests for:
   - deferred source execution after barrier open;
   - timeout when barrier is forced closed.

Code target:

- control-plane root nodes/services + lifecycle plan assembly.

## Phase E — Startup observability

Goal:

- trace/log/metric visibility for all startup stages.

Tests:

1. startup nodes emit lifecycle log messages to root stdout dispatch rail.
2. discovery/config/barrier latencies are exported as metrics.
3. traces include startup stage transitions without recursion loops.

Code target:

- lifecycle root logging services
- observability system node routing for startup events.

## Phase F — Long-running daemon mode

Goal:

- startup model reused for API-driven perpetual runner.

Tests:

1. root pulse enqueued once, runner enters long-running loop.
2. request envelopes accepted only after startup barrier open.
3. control-plane stop performs graceful drain and exits cleanly.

Code target:

- runtime runner ingress/orchestration services.

## Exit criteria

1. No bootstrap hardcode path required for discovery/config initialization.
2. Root/leaf startup semantics are message-driven and test-covered.
3. Startup cannot proceed with partial discovery/config state.
4. Async root runner policy is explicit and regression-protected.

## Follow-up

Remaining startup migration gaps are tracked in:

- [Platform startup full-rails gap closure (TDD plan)](platform_startup_full_rails_gap_closure_tdd_plan.md)
