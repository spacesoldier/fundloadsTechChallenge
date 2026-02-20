# Engine runtime contract cleanup (schema + routing service + execution purity)

## Goal

Complete the next cleanup wave after engine target-model Step F:

- enforce strict runtime config schema (explicit allow-list keys only);
- remove legacy `runtime.pipeline` special-case code path;
- rename `RoutingPort` service to `RoutingService` and separate module responsibilities;
- remove reply-correlation policy from `SyncRunner` hot path;
- remove compatibility tails (`route_pairs`, legacy list routing result, waiter fallbacks);
- keep deterministic behavior and parity outputs unchanged.

This plan is a continuation of:

- [engine_runner_router_target_model_tdd_plan](engine_runner_router_target_model_tdd_plan.md)
- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

---

## Current issues (entry point)

1. Runtime still has dedicated legacy rejection path (`reject_runtime_pipeline`) instead of strict schema policy.
2. `SyncRunner` still calls reply-correlation service directly (`register_if_requested`, `complete_if_waiting`).
3. Naming drift: service contract is called `RoutingPort` although behavior is service-level orchestration.
4. Compatibility tails remain in hot/runtime paths:
   - runner accepts list-based routing result fallback;
   - `route_pairs()` exists as migration API;
   - legacy coordinator/waiter fallback paths still exist.
5. Module structure has grown organically and needs explicit boundaries.

---

## Target model

### 1) Runtime schema is strict by default

- `runtime` accepts only known top-level keys:
  - `strict`
  - `discovery_modules`
  - `platform`
  - `ordering`
  - `web`
- unknown keys fail in validator with deterministic config error;
- no runtime-level special-casing for removed legacy fields.

### 2) Routing layer naming and boundaries

- `Router` remains pure routing core (`routing/router.py`): no DI, no registry access;
- DI-backed routing facade is `RoutingService` (currently `RoutingPort`);
- execution code injects routing service contract, not `*Port` naming.

### 3) Runner is execution-only

`SyncRunner` responsibilities:

- dequeue envelope;
- load context;
- invoke node;
- request routing for outputs;
- enqueue local deliveries.

`SyncRunner` does not:

- register reply waiters;
- complete terminal replies;
- adapt legacy routing result contracts.

### 4) Reply correlation is outside runner

- reply correlation is handled by platform-level observability/reply integration:
  - ingress event handling (`reply_to` registration);
  - terminal event handling (correlated completion).
- no per-hop direct correlation checks in runner.

### 5) Compatibility tails removed

- remove list-based route-result fallback in runner;
- remove `route_pairs()` migration API;
- remove legacy waiter/coordinator fallback adapters in builder/lifecycle.

---

## Proposed module structure adjustments

Target direction (incremental, no big-bang move):

- `stream_kernel/routing/router.py`:
  - `RoutingResult`
  - `Router` (pure rules)
- `stream_kernel/routing/routing_service.py`:
  - `RoutingService` (DI facade over consumer registry + Router)
- `stream_kernel/integration/`:
  - keep transport adapters (`work_queue`, `kv_store`, etc.)
  - no routing facade module in this package

---

## TDD sequence

### Step A — Runtime strict schema allow-list (RED -> GREEN)

Status:

- [x] Completed on February 13, 2026.
- Added validator contract coverage:
  - `RUNTIME-SCHEMA-01`: unknown runtime top-level key fails.
  - `RUNTIME-SCHEMA-03`: `runtime.pipeline` fails via unknown runtime key path.
- Implemented:
  - explicit runtime top-level allow-list validation in `config/validator.py`;
  - strict runtime mapping checks for optional `runtime.tracing` and `runtime.cli`;
  - removed builder-level `reject_runtime_pipeline(...)` special-case path.
- Runtime-level tests updated:
  - `run_with_config` no longer rejects `runtime.pipeline` through dedicated builder logic;
  - `run(...)` rejects `runtime.pipeline` through config validation.
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py tests/stream_kernel/app/test_framework_run.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_builder.py`

#### RED

- add validator tests:
  - `RUNTIME-SCHEMA-01`: unknown `runtime` top-level key fails;
  - `RUNTIME-SCHEMA-02`: known keys still pass;
  - `RUNTIME-SCHEMA-03`: `runtime.pipeline` fails as unknown key (not special-cased path).

#### GREEN

- implement explicit runtime top-level allow-list in `config/validator.py`;
- remove `reject_runtime_pipeline(...)` call and function from builder;
- keep deterministic error message category in validator.

#### REFACTOR

- update docs mentioning runtime legacy removal path.

---

### Step B — RoutingService rename and module split (RED -> GREEN)

Status:

- [x] Completed on February 13, 2026 (hard cut, no compatibility shim).
- Implemented:
  - canonical routing facade module: `src/stream_kernel/routing/routing_service.py`
    with `RoutingService`;
  - removed legacy facade module: `src/stream_kernel/integration/routing_port.py`;
  - `SyncRunner` now requests `inject.service(RoutingService)` and uses `RoutingService`
    runtime guard contract.
- Added/updated tests:
  - `tests/stream_kernel/integration/test_routing_port.py` (routing facade contract on new module path);
  - `tests/stream_kernel/execution/test_runner_interface.py`
    (`SyncRunner` uses `RoutingService` injection contract).
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/execution/test_runner_interface.py tests/stream_kernel/execution/test_builder.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_runner_routing_integration.py tests/stream_kernel/execution/test_runner_reply_correlation.py tests/stream_kernel/execution/test_engine_target_model_contract.py`

#### RED

- add/adjust tests for:
  - DI resolves `RoutingService` contract;
  - runner uses `inject.service(RoutingService)`;
  - no imports from `stream_kernel.integration.routing_port` remain.

#### GREEN

- introduce `stream_kernel/routing/routing_service.py` with `RoutingService`;
- migrate usages from `RoutingPort` to `RoutingService`;
- remove legacy routing facade module from `integration/`.

#### REFACTOR

- update docs and plan references to new naming.

---

### Step C — Remove reply policy from runner (RED -> GREEN)

Status:

- [x] Completed on February 13, 2026.
- Implemented:
  - `SyncRunner` no longer depends on `ReplyCoordinatorService` directly;
  - ingress/terminal reply correlation moved behind observability hooks
    (`on_ingress`, `on_terminal_event`);
  - platform binding now wraps fan-out observability with
    `ReplyAwareObservabilityService` (DI-injected coordinator);
  - legacy waiter constructor path is preserved only as a transitional
    observability wrapper (`legacy_reply_aware_observability`), not as runner
    hot-path logic.
- Updated tests:
  - runner reply-correlation tests now exercise observability-mediated policy;
  - builder observability binding test now validates reply-aware wrapper wiring;
  - runner contract test asserts no `reply_coordinator` field in `SyncRunner`.

#### RED

- contract tests:
  - `RUNNER-PURITY-01`: runner has no reply registration call;
  - `RUNNER-PURITY-02`: runner has no terminal completion call;
  - `RUNNER-PURITY-03`: correlated reply still works end-to-end.

#### GREEN

- move correlation to platform observer/reply integration service;
- runner emits lifecycle events only (ingress + terminal), no direct reply API usage;
- maintain existing reply behavior via new service hooks.

#### REFACTOR

- remove `reply_coordinator` field from runner constructor/injection.

---

### Step D — Remove compatibility tails (RED -> GREEN)

Status:

- [x] Completed on February 13, 2026.
- Implemented:
  - removed `RoutingService.route_pairs(...)` migration API;
  - hardened `SyncRunner` routing contract to accept `RoutingResult` only
    (no list-based fallback path);
  - removed legacy waiter handoff from `run_with_sync_runner(...)`
    (`_legacy_reply_waiter` deleted);
  - removed `ReplyWaiterService -> ReplyCoordinatorService` fallback adapter
    from lifecycle orchestration (`_resolve_reply_coordinator` now coordinator-only);
  - updated execution/integration tests and stubs to structured `RoutingResult`
    and coordinator-only reply completion path.
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution`
  - `.venv/bin/pytest -q tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/app/test_framework_run.py tests/stream_kernel/app/test_framework_consumer_registry.py`

#### RED

- tests fail when legacy APIs are used:
  - list-based route result in runner;
  - `route_pairs` call sites;
  - legacy waiter fallback adapter usage.

#### GREEN

- runner accepts only `RoutingResult`;
- remove `route_pairs()` from routing service;
- delete legacy fallback helpers from builder/lifecycle.

#### REFACTOR

- simplify related tests and fixtures to single contract path.

---

### Step E — Boundary and terminal contract hardening (RED -> GREEN)

Status:

- [x] Completed on February 13, 2026.
- Implemented:
  - process-supervisor boundary hook normalized to structured routing contract:
    `BootstrapSupervisor.execute_boundary(...) -> RoutingResult`;
  - lifecycle orchestration now validates boundary result channels explicitly:
    - `local_deliveries` must be empty;
    - `boundary_deliveries` must be empty;
    - `terminal_outputs` must contain `Envelope` items only;
  - parent-side terminal correlation keeps deterministic completion semantics via
    `ReplyCoordinatorService` on validated `terminal_outputs`;
  - local bootstrap supervisor returns structured `RoutingResult` in both in-process
    and child-boundary paths.
- Added contract coverage:
  - `BOUNDARY-CONTRACT-01`: reject `local_deliveries` on boundary result;
  - `BOUNDARY-CONTRACT-02`: reject nested `boundary_deliveries`;
  - `BOUNDARY-CONTRACT-03`: reject non-`Envelope` terminal outputs.
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution`
  - `.venv/bin/pytest -q tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/app/test_framework_run.py tests/stream_kernel/app/test_framework_consumer_registry.py`

#### RED

- add tests ensuring:
  - local vs boundary vs terminal channels stay explicit;
  - no hidden reply side-effects in runner path;
  - process-supervisor path still completes terminal correlation deterministically.

#### GREEN

- align lifecycle orchestration hooks with routing result contract;
- keep deterministic diagnostics categories.

#### REFACTOR

- remove dead branches in orchestration helpers.

---

### Step F — Regression/parity gate

- run focused suites:
  - runner/routing/lifecycle/reply contracts;
  - process-supervisor + handoff suites;
  - CLI deterministic baseline + experiment parity (`jq -cS` + `diff -u`).
- update reports and plan statuses.

Status:

- [x] Completed on February 13, 2026 (regression/parity gate passed).
- Focused runner/routing/lifecycle/reply suites:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_runner_interface.py tests/stream_kernel/execution/test_runner_context_integration.py tests/stream_kernel/execution/test_runner_routing_integration.py tests/stream_kernel/execution/test_runner_reply_correlation.py tests/stream_kernel/execution/test_engine_target_model_contract.py tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/app/test_framework_consumer_registry.py`
- Process-supervisor + handoff and compatibility suites:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_child_bootstrap.py tests/stream_kernel/execution/test_builder.py tests/stream_kernel/execution/test_reply_waiter_contract.py tests/stream_kernel/execution/test_secure_tcp_transport.py tests/stream_kernel/execution/test_bootstrap_keys.py tests/stream_kernel/app/test_framework_run.py tests/stream_kernel/config/test_newgen_validator.py tests/stream_kernel/integration/test_work_queue.py`
- Deterministic integration suites:
  - `.venv/bin/pytest -q tests/integration/test_end_to_end_baseline_limits.py tests/integration/test_end_to_end_experiment_features.py tests/usecases/steps/test_compute_features.py`
- Memory-profile CLI parity:
  - baseline parity: `jq -cS` + `diff -u` against `docs/analysis/data/assets/output.txt` is empty;
  - experiment parity: `jq -cS` + `diff -u` against `docs/analysis/data/assets/output_exp_mp.txt` is empty.
- Evidence report:
  - [engine_runtime_contract_cleanup_stepf_regression_parity_report](engine_runtime_contract_cleanup_stepf_regression_parity_report.md)

---

## Test matrix (initial draft)

- `RUNTIME-SCHEMA-01..03`
- `ROUTING-SVC-01..03`
- `RUNNER-PURITY-01..03`
- `TAIL-CLEANUP-01..04`
- `BOUNDARY-CONTRACT-01..03`
- `STEP-F-REGRESSION-01..03`

---

## Integration with master plans

1. Web master plan keeps this as immediate engine-cleanup prerequisite before FastAPI/runtime-web integration expansion.
2. Engine target-model plan is considered complete for its original scope and delegates next cleanup wave to this file.
3. Work index includes this plan as active track.

---

## Exit criteria

- runtime top-level schema is strict and explicit;
- no builder legacy-runtime special-case path remains;
- routing DI contract is renamed and consistently used as service;
- runner has execution-only responsibilities;
- legacy compatibility paths are removed;
- deterministic outputs and process-supervisor behavior remain unchanged.
