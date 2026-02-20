# Web Phase 2: secure TCP runtime integration and lifecycle TDD plan

## Purpose

Integrate Phase 1 secure TCP transport into framework runtime execution path
without runtime hardcode, while introducing deterministic process lifecycle
control for web/execution split.

This is a focused sub-plan for:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- [web_phase1_secure_tcp_transport_tdd_plan](web_phase1_secure_tcp_transport_tdd_plan.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

Current status:

- [x] Step A complete (`IPC-INT-01..04`): runtime profile summary, transport wiring switch, tcp_local baseline path enabled.
- [x] Step B complete (`PROC-INT-01..04`): lifecycle order/ready/stop/crash behavior is test-covered.
- [x] Step C complete (`IPC-INT-05..08`): tcp_local boundary rejects invalid/oversized/replay frames and hides secrets in diagnostics.
- [x] Step D complete (`IPC-INT-09..12`): runtime transport profile is represented as DI-bound `RuntimeTransportService`.
- [x] Step E complete (`PROC-INT-05..08`): lifecycle orchestration extracted from builder with explicit runtime error taxonomy.
- [x] Step F complete: regression/parity gate executed and documented.
- [x] Documentation sync in main web plan and architecture docs.

---

## Scope of Phase 2

- Runtime-level transport profile integration:
  - memory profile remains default;
  - `execution_ipc.transport=tcp_local` enables secure transport path.
- Framework lifecycle service for execution workers:
  - deterministic startup order;
  - readiness/health checks;
  - graceful shutdown with drain contract.
- Builder/runtime integration through DI/discovery only.

Out of scope:

- DAG partition across process groups (Phase 3);
- full request/reply waiter protocol across process boundary (Phase 4);
- FastAPI endpoint adapters (Phase 5).

---

## Target integration contract (phase baseline)

### Runtime integration points

- `runtime_contract_summary(...)` includes active execution transport profile.
- `build_runtime_artifacts(...)` resolves transport/lifecycle services from config.
- `execute_runtime_artifacts(...)` runs through same API for memory and tcp profiles.

### Lifecycle contract

- platform lifecycle service exposes:
  - `start()`
  - `ready(timeout)`
  - `stop(graceful_timeout)`
  - `status()`
- startup sequence is deterministic:
  1) build-time registries and DI;
  2) runtime services;
  3) execution workers;
  4) ingress ready signal.

### Compatibility contract

- Existing deterministic fund_load run path remains green in memory profile.
- No change in business-node contracts.
- No direct runtime monkeypatch points for transport/lifecycle.

---

## TDD sequence

### Step A — RED tests: runtime transport profile wiring

- `IPC-INT-01`: memory profile (no `execution_ipc`) keeps in-process execution path.
- `IPC-INT-02`: `execution_ipc.transport=tcp_local` selects secure transport integration path.
- `IPC-INT-03`: runtime summary reports active transport profile deterministically.
- `IPC-INT-04`: tcp_local profile builds through framework lifecycle defaults (no manual wiring).

### Step B — RED tests: lifecycle manager behavior

- `PROC-INT-01`: deterministic startup order (services before workers).
- `PROC-INT-02`: ready state required before accepting ingress.
- `PROC-INT-03`: graceful shutdown drains in-flight messages within timeout.
- `PROC-INT-04`: worker crash surfaces deterministic runtime error category.

### Step C — RED tests: boundary behavior and safety

- `IPC-INT-05`: invalid signed frame is rejected and does not enter runner queue.
- `IPC-INT-06`: oversized frame is rejected at boundary and counted as transport reject.
- `IPC-INT-07`: replayed nonce rejected in runtime transport path (not only unit transport).
- `IPC-INT-08`: transport secret never appears in logs/diagnostics.

Detailed Step C execution plan:

1. Boundary contract freeze
   - tcp-local queue/topic adapters must accept framed wire input at boundary;
   - boundary decode/verify must use `secure_tcp_transport` contract from Phase 1;
   - rejected frames must never be enqueued.
2. Runtime wiring contract
   - `ensure_runtime_transport_bindings(...)` must build tcp-local queue/topic with secure transport instance;
   - transport settings come from `runtime.platform.execution_ipc.*`;
   - memory profile behavior remains unchanged.
3. Error taxonomy and safety
   - boundary errors are deterministic and profile-specific (`tcp_local transport reject`);
   - rejection counters are observable in adapter runtime state for tests/diagnostics;
   - secret material is not present in error messages.
4. TDD sequence
   - RED: add `IPC-INT-05..08` tests against runtime queue bindings for tcp_local profile;
   - GREEN: implement boundary decode/verify + rejection counters;
   - REFACTOR: isolate transport-config parsing helper and sanitize error paths.
5. Exit criteria for Step C
   - all `IPC-INT-05..08` tests pass;
   - no regressions in existing `IPC-INT-01..04` and `PROC-INT-01..04`;
   - no behavior changes for memory profile.

### Step D — GREEN implementation

- Introduce runtime transport service abstraction and bind by profile.
- Integrate secure transport into execution builder/runtime flow via DI.
- Add lifecycle service and wire startup/shutdown hooks.
- Keep memory profile as explicit fallback path.

Detailed Step D execution plan:

1. Runtime transport service contract
   - add platform runtime transport service contract with stable API:
     - `profile: str`
     - `build_queue()`
     - `build_topic()`
   - keep transport-profile internals (`memory`, `tcp_local`) behind this service.
2. DI-bound profile selection
   - builder resolves profile from runtime config exactly once;
   - builder registers one runtime transport service instance into scenario DI;
   - queue/topic bindings are produced through that service instead of direct
     profile branches in queue/topic registration logic.
3. Queue/topic parity contract
   - memory profile keeps `InMemoryQueue` / `InMemoryTopic`;
   - `tcp_local` keeps secure boundary path (`TcpLocalQueue` / `TcpLocalTopic`)
     with shared secure transport settings from runtime config.
4. Lifecycle integration checkpoint
   - lifecycle orchestration remains under `RuntimeLifecycleManager`;
   - `execute_runtime_artifacts(...)` keeps deterministic `start -> ready -> run -> stop`
     semantics for `tcp_local`.
5. Step D TDD cases
   - `IPC-INT-09`: runtime transport service is registered in DI for memory profile.
   - `IPC-INT-10`: runtime transport service is registered in DI for `tcp_local`.
   - `IPC-INT-11`: queue/topic factories for `tcp_local` are produced via transport service.
   - `IPC-INT-12`: memory profile queue/topic parity remains unchanged.
6. Exit criteria for Step D
   - `IPC-INT-09..12` pass;
   - existing `IPC-INT-01..08` and `PROC-INT-01..04` stay green;
   - no runtime hardcoded profile branches outside transport service creation.

Step D completion note:

- `ensure_runtime_transport_bindings(...)` now registers one
  `RuntimeTransportService` in DI and derives queue/topic factories from it;
- memory and `tcp_local` profiles are encapsulated in transport-service implementations;
- `IPC-INT-09..12` are covered in `tests/stream_kernel/execution/test_builder.py`.

Execution breakdown for remaining Phase 2 work:

1. Step C transport-boundary RED:
   - inject secure transport at queue/topic boundary for tcp_local profile;
   - assert invalid frame rejection and non-delivery into runner queue;
   - assert secret redaction in error diagnostics.
2. Step D GREEN integration:
   - wire `secure_tcp_transport` service into tcp_local queue/topic adapters;
   - replace baseline local queue shim behavior with signed-frame path;
   - keep deterministic fallback for memory profile.
3. Step E refactor:
   - extract lifecycle orchestration service from builder helpers;
   - normalize runtime error categories for lifecycle/transport boundary.
4. Step F regression/parity:
   - run focused IPC/lifecycle suites + baseline deterministic scenarios;
   - validate parity of outputs in memory profile after transport integration.

### Step E — REFACTOR

- Extract profile selection and lifecycle orchestration into focused services.
- Normalize runtime error classes for transport/lifecycle failures.
- Remove transitional helpers after parity tests are green.

Detailed Step E execution plan:

1. Lifecycle orchestration extraction
   - move lifecycle resolve/start/ready/stop execution flow from
     `execution.builder` helper functions into a dedicated execution module;
   - keep `builder.execute_runtime_artifacts(...)` as thin orchestrator selecting
     profile and delegating lifecycle-managed execution path.
2. Runtime error taxonomy
   - introduce explicit runtime exceptions for lifecycle/process boundary:
     - `RuntimeLifecycleResolutionError`
     - `RuntimeLifecycleReadyError`
     - `RuntimeWorkerFailedError`
   - keep deterministic and non-secret diagnostics in error messages.
3. Test boundary cleanup
   - remove test dependency on private builder helpers
     (`_resolve_runtime_lifecycle_manager`, `_runtime_lifecycle_policy`);
   - switch tests to DI-driven lifecycle injection via `ScenarioScope`.
4. Step E TDD cases
   - `PROC-INT-05`: lifecycle orchestration path is exercised without monkeypatching
     private builder helpers.
   - `PROC-INT-06`: missing lifecycle binding raises `RuntimeLifecycleResolutionError`.
   - `PROC-INT-07`: not-ready lifecycle raises `RuntimeLifecycleReadyError`.
   - `PROC-INT-08`: worker crash raises `RuntimeWorkerFailedError`.
5. Exit criteria for Step E
   - `PROC-INT-05..08` pass;
   - existing `IPC-INT-01..12` and `PROC-INT-01..04` stay green;
   - lifecycle orchestration code no longer implemented as private builder helpers.

Step E completion note:

- `execute_runtime_artifacts(...)` now delegates tcp-local lifecycle flow to
  `execution.lifecycle_orchestration.execute_with_runtime_lifecycle(...)`;
- private builder helper points (`_resolve_runtime_lifecycle_manager`,
  `_runtime_lifecycle_policy`) were removed;
- runtime error categories are explicit:
  `RuntimeLifecycleResolutionError`, `RuntimeLifecycleReadyError`,
  `RuntimeWorkerFailedError`;
- tests were switched to DI-driven lifecycle injection without monkeypatching
  private builder helper hooks.

### Step F — parity and regression gate

- run focused web/transport/lifecycle suites;
- run baseline deterministic integration suite;
- confirm output parity for memory profile.

Detailed Step F execution plan:

1. Focused suite gate
   - run runtime transport/lifecycle suites (`execution`, `app runtime`, `validator`, `work_queue`);
   - verify no regressions after Step E refactor.
2. Baseline deterministic integration gate
   - run end-to-end baseline/experiment integration tests;
   - ensure deterministic acceptance/rejection behavior remains unchanged.
3. CLI parity gate (reference assets)
   - produce baseline output via CLI from reference input;
   - compare with reference output using normalized `jq -cS` + `diff -u`;
   - repeat for experiment config/reference output.
4. Findings handling
   - any regression found during parity run is fixed immediately under TDD;
   - rerun full Step F suite after fix.
5. Exit criteria for Step F
   - focused suites green;
   - integration suites green;
   - normalized parity diff is empty for baseline and experiment.

Step F completion note:

- detailed execution log is captured in:
  [web_phase2_stepf_regression_parity_report](web_phase2_stepf_regression_parity_report.md);
- focused/runtime and integration suites are green;
- CLI parity against `docs/analysis/data/assets/output*.txt` is green;
- one parity regression (`Decimal * float` in `ComputeFeatures`) was fixed with
  dedicated test coverage.

---

## Documentation sync checklist

- Update:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md) status section
  - [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)
- Cross-link with:
  - [network_interfaces_expansion_plan](network_interfaces_expansion_plan.md)

---

## Done criteria

- All `IPC-INT-*` and `PROC-INT-*` Phase 2 tests pass.
- Secure transport is integrated into runtime path via config+DI (no hardcoded bypass).
- Lifecycle start/ready/stop semantics are deterministic and test-covered.
- Memory fallback profile remains green and deterministic.
- Main plan progress notes are synchronized.
