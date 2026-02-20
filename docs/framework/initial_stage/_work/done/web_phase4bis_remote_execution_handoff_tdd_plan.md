# Web Phase 4bis: remote execution handoff TDD plan

## Purpose

Close the remaining execution gap after Phase 4:

- reply correlation is already deterministic;
- process-supervisor contracts are in place;
- but cross-group workloads are not yet executed by real child runtimes end-to-end.

This phase introduces true parent->child execution handoff for `process_supervisor`
without breaking `memory` profile behavior.

Parent plan:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

Related references:

- [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)
- [web_phase4_reply_correlation_tdd_plan](web_phase4_reply_correlation_tdd_plan.md)

---

## Review snapshot (current state)

Already implemented:

- secure `tcp_local` transport with auth/replay/ttl/size guards;
- bootstrap supervisor lifecycle and child bootstrap metadata contract;
- reply waiter + terminal event correlation in runtime flow;
- boundary execution hook (`execute_boundary`) in supervisor contract.

Remaining gap:

- runtime still lacks full cross-group execution dispatch to child worker loops as
  the default path in `process_supervisor` profile.

Phase 4bis status: complete (Step A RED complete, Steps B-F GREEN complete).

---

## Scope

In scope:

- deterministic cross-group handoff contract;
- remote execution dispatch path through secure transport;
- child runtime processing contract;
- terminal response propagation back to parent correlation flow;
- deterministic failure mapping for child crash/timeout.

Out of scope:

- FastAPI endpoint registration (Phase 5);
- streaming/GraphQL capabilities (Phase 6);
- Redis backend parity (Phase 8).

---

## Target contracts

### 1) Placement -> dispatch contract

- each executable node resolves to exactly one process group;
- cross-group edge produces transport hop with preserved `trace_id` and `reply_to`;
- same-group edge stays local.

### 2) Parent boundary dispatcher contract

- parent enqueues execution payload to target child group channel;
- parent waits for terminal envelope or timeout/cancel path;
- terminal envelope carries original `trace_id`.

### 3) Child runtime contract

- child process has isolated DI/discovery/runtime scope;
- child consumes incoming execution envelopes and runs assigned node set;
- child emits terminal/output envelopes through transport backchannel.

### 4) Failure contract

- child timeout/crash => deterministic runtime error category;
- no secret leakage in boundary diagnostics;
- graceful stop still drains in-flight workloads when configured.

---

## TDD sequence

### Step A — contract freeze + RED tests

- Freeze cross-group handoff invariants in tests:
  - `HANDOFF-01` cross-group node dispatched to target child group.
  - `HANDOFF-02` same-group node does not use transport hop.
  - `HANDOFF-03` remote terminal completes waiter by original `trace_id`.
  - `HANDOFF-04` child timeout/crash maps to deterministic runtime worker error.
  - `HANDOFF-05` graceful drain preserves in-flight completion semantics.

### Step B — parent dispatch implementation (GREEN)

- Implement boundary dispatcher in parent runtime path.
- Preserve metadata (`trace_id`, `reply_to`, scenario/run identity).

### Step C — child execution loop wiring (GREEN)

- Wire child consume->execute->emit loop to transport boundary.
- Ensure child runtime uses its own DI scope and discovered node set.

### Step D — reply completion integration (GREEN)

- Complete parent waiters from child terminal outputs.
- Keep duplicate/late terminal handling deterministic.

### Step E — failure/timeout hardening (REFACTOR)

- Add deterministic mapping for timeout/crash/force-stop outcomes.
- Add sanitized diagnostics for handoff failures.

### Step F — regression/parity gate

- Run focused handoff suites.
- Run Phase 2/3/4 compatibility suites.
- Confirm memory-profile parity (`jq -cS` + `diff -u`) remains unchanged.

---

## Documentation sync checklist

- Update status in:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- Align architecture summary in:
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)

---

## Done criteria

- `HANDOFF-01..05` are green.
- cross-group execution runs through real child runtime path.
- parent correlation behavior remains deterministic.
- memory profile behavior/parity remains unchanged.

---

## Step A progress note

- [x] Contract freeze for remote-handoff invariants is captured in `HANDOFF-01..05`.
- [x] Dedicated RED suite is added:
  - `tests/stream_kernel/execution/test_remote_handoff_contract.py`
- [x] RED execution evidence:
  - command:
    - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py`
  - result:
    - `5 failed`
    - failures correspond to `HANDOFF-01..05` contract gaps:
      - missing dispatch-group metadata model on boundary inputs;
      - same-group local path not separated from boundary dispatch path;
      - missing parent-child trace correlation mapping for terminal completion;
      - generic worker error category for child failure path;
      - no explicit boundary-drain wait hook before group stop.

## Step B progress note

- [x] Parent dispatch path is activated in process-supervisor runtime flow.
- [x] Boundary inputs now carry explicit dispatch metadata contract:
  - `BoundaryDispatchInput(payload, dispatch_group, trace_id, reply_to)`.
- [x] Same-group profile (`single process_group`) stays local and bypasses boundary dispatch.
- [x] Boundary terminal correlation now supports alias mapping for child trace ids.
- [x] Child-boundary failures now map to explicit remote-handoff worker error wording.
- [x] Optional boundary-drain wait hook is executed before stop phase.
- [x] GREEN evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py`
  - result: `5 passed`
- [x] Compatibility evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_builder.py -k 'process_supervisor or PH4-D or STOP-IPC or KEY-IPC or CHILD-BOOT'`
  - result: all passed.

Step B implementation map:

1. Parent dispatch and boundary activation:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_build_boundary_dispatch_inputs(...)` and `BoundaryDispatchInput`.
2. Local-vs-boundary split:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - boundary path activates only for multi-group process-supervisor profiles.
3. Correlation alias and failure category:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_complete_reply_waiters_from_terminal_outputs(..., trace_aliases=...)`
   - remote-handoff error message path in boundary exception branch.
4. Pre-stop boundary drain hook:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_wait_boundary_drain_if_available(...)`.
5. TDD coverage:
   - `tests/stream_kernel/execution/test_remote_handoff_contract.py`
   - `HANDOFF-01..05`.

## Step C progress note

- [x] Child consume->execute->emit loop is wired through child bootstrap runtime API.
- [x] Child runtime loop resolves discovered node targets from child `ApplicationContext`.
- [x] Child boundary execution is callable from supervisor boundary path when child bundle is present.
- [x] Dispatch-group filtering is enforced inside child loop for mixed boundary batches.
- [x] GREEN evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_child_bootstrap.py`
  - result: `6 passed`
- [x] Compatibility evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_builder.py -k 'process_supervisor or PH4-D or STOP-IPC or KEY-IPC or CHILD-BOOT or HANDOFF'`
  - result: all passed.

Step C implementation map:

1. Child boundary execution API:
   - `src/stream_kernel/execution/child_bootstrap.py`
   - `execute_child_boundary_loop_from_bundle(...)`
   - `execute_child_boundary_loop(...)`
2. Child dispatch input normalization:
   - `src/stream_kernel/execution/child_bootstrap.py`
   - `ChildBoundaryInput` + `_normalize_child_boundary_input(...)`.
3. Discovered-node execution map in child runtime:
   - `src/stream_kernel/execution/child_bootstrap.py`
   - `_build_child_node_map(...)` + `_build_child_step_callable(...)`.
4. Supervisor wiring:
   - `src/stream_kernel/platform/services/bootstrap.py`
   - `LocalBootstrapSupervisor.load_child_bootstrap_bundle(...)`
   - `LocalBootstrapSupervisor.execute_boundary(...)` delegates to child loop when available.
5. Parent boundary payload extension:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `BoundaryDispatchInput` now carries `target` for child node resolution.
6. TDD coverage:
   - `tests/stream_kernel/execution/test_child_bootstrap.py`
   - `HANDOFF-C-01..03` child loop coverage.

## Step D progress note

- [x] Parent waiter completion from child terminal outputs is verified on boundary path.
- [x] Duplicate child terminal events are deterministic:
  - first terminal wins;
  - subsequent duplicates are ignored and counted.
- [x] Late child terminal events without in-flight waiter are deterministically dropped.
- [x] GREEN evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py`
  - result: `7 passed`
- [x] Compatibility evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_child_bootstrap.py tests/stream_kernel/execution/test_builder.py -k 'process_supervisor or PH4-D or STOP-IPC or KEY-IPC or CHILD-BOOT'`
  - result: all passed.

Step D contract confirmation:

1. Duplicate terminal determinism:
   - child may emit repeated terminal for same logical request (`child:<trace_id>` alias);
   - parent completes waiter exactly once;
   - duplicate terminal is recorded as `duplicate_terminal`, not replayed to caller.
2. Late terminal determinism:
   - terminal arriving after waiter is absent/closed is dropped;
   - no synthetic completion is re-created;
   - drop is recorded as `late_reply_drop`.
3. Correlation boundary:
   - parent correlation key remains original caller `trace_id`;
   - child-local trace aliases are mapped only for completion bridge and never exposed as caller-facing identity.

Step D execution flow (boundary terminal -> waiter):

1. Parent dispatch records alias mapping for boundary hop (`child:<trace_id>` -> `<trace_id>`).
2. Boundary execution returns terminal envelope list.
3. Completion bridge normalizes child trace to parent trace.
4. Reply waiter `complete()` enforces deterministic duplicate/late semantics.
5. Counters are available for diagnostics and further observability wiring.

Step D implementation map:

1. Parent completion path:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_complete_reply_waiters_from_terminal_outputs(...)` with trace alias support.
2. Deterministic completion semantics:
   - `src/stream_kernel/platform/services/reply_waiter.py`
   - duplicate/late handling remains stable on `complete(...)`.
3. TDD coverage:
   - `tests/stream_kernel/execution/test_remote_handoff_contract.py`
   - `HANDOFF-D-01` duplicate child terminal behavior;
   - `HANDOFF-D-02` late child terminal drop behavior.

## Step E progress note

- [x] Remote handoff failure mapping is hardened with deterministic categories:
  - timeout => `"remote handoff timed out for group '<group>'"`;
  - transport => `"remote handoff transport failed for group '<group>'"`;
  - generic boundary failure => `"remote handoff failed for group '<group>'"`.
- [x] Sanitized failure diagnostics are emitted through optional supervisor hook:
  - `emit_handoff_failure(group_name, category)`;
  - no raw exception text/secret payload is propagated through this diagnostics channel.
- [x] Primary remote handoff failure is no longer masked by shutdown fallback failures:
  - if boundary execution already failed, stop/force-terminate failure is suppressed as secondary;
  - shutdown fallback still emits deterministic `shutdown` diagnostics category.
- [x] GREEN evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py`
  - result: `10 passed`
- [x] Compatibility evidence:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_child_bootstrap.py tests/stream_kernel/execution/test_builder.py -k 'process_supervisor or PH4-D or STOP-IPC or KEY-IPC or CHILD-BOOT'`
  - result: all passed.

Step E implementation map:

1. Failure category mapping + sanitized diagnostics:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_map_worker_failure(...)` and `_emit_handoff_failure_diagnostic(...)`.
2. Primary error precedence over shutdown error:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `primary_error` / `stop_error` split in `execute_with_bootstrap_supervisor(...)`.
3. Supervisor diagnostics hook contract:
   - `src/stream_kernel/platform/services/bootstrap.py`
   - `BootstrapSupervisor.emit_handoff_failure(...)`.
4. TDD coverage:
   - `tests/stream_kernel/execution/test_remote_handoff_contract.py`
   - `HANDOFF-E-01` timeout category + sanitization;
   - `HANDOFF-E-02` transport category + sanitization;
   - `HANDOFF-E-03` primary handoff failure precedence over shutdown failure.

## Step F progress note

- [x] Focused handoff suites executed and green:
  - `tests/stream_kernel/execution/test_remote_handoff_contract.py`
  - `tests/stream_kernel/execution/test_child_bootstrap.py`
  - `tests/stream_kernel/execution/test_builder.py` (handoff/process-supervisor slices).
- [x] Phase 2/3/4 compatibility suites executed and green:
  - reply/correlation, secure transport, bootstrap keys, runtime run/config/work-queue slices.
- [x] Memory-profile parity remains unchanged:
  - baseline and experiment outputs are equal to reference assets after `jq -cS` normalization.
- [x] Tcp-local reject guards remain green:
  - replay / oversized / reject checks.

Step F evidence report:

- [web_phase4bis_stepf_regression_parity_report](web_phase4bis_stepf_regression_parity_report.md)
