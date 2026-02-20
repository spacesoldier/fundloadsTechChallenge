# Web Phase 4: cross-process reply correlation TDD plan

## Purpose

Implement deterministic request/reply delivery across process boundaries for the
web track.

This phase continues after Phase 3 and closes the gap between:

- secure transport/lifecycle contracts (already present),
- and actual caller-facing correlated response delivery.

Primary parent plan:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

Related docs:

- [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)
- [Runner loop orchestration](../web/Runner%20loop%20orchestration.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

---

## Review snapshot (current state)

What is already in place:

- [x] Signed transport envelope includes `trace_id` and `reply_to`.
- [x] Runtime transport profile (`memory` / `tcp_local`) is DI-bound.
- [x] Bootstrap supervisor contracts exist (`start_groups`, `wait_ready`, `stop_groups`).
- [x] Child bootstrap metadata contract exists.

What is still missing for Phase 4 objective:

- [x] Reply waiter service contract + implementation in DI.
- [x] Deterministic terminal event contract (success/error/cancel).
- [x] Runtime path that delivers terminal results back to registered waiters.
- [x] Timeout/cancel cleanup semantics with leak guards.
- [x] Duplicate terminal event deduplication behavior.
- [ ] Full remote child-process execution handoff in default runtime profile.

Phase 4 status: in progress (Steps A-F complete for correlation scope; remote child-process handoff still pending).

Residual handoff work is moved to:

- [web_phase4bis_remote_execution_handoff_tdd_plan](web_phase4bis_remote_execution_handoff_tdd_plan.md)

---

## Scope of Phase 4

In scope:

- request/reply correlation service contract;
- waiter lifecycle (register, complete, timeout, cancel, cleanup);
- terminal event semantics (success/error/cancel);
- deterministic behavior for duplicates and late replies;
- runtime wiring from ingress -> execution -> correlated egress.

Out of scope:

- full FastAPI endpoint registration (Phase 5);
- streaming HTTP/2 and GraphQL expansion (Phase 6);
- Redis parity rollout (Phase 8 in parent plan).

---

## Target contracts

### 1) Reply waiter service contract

Platform service contract (DI-bound) should support:

- register waiter by correlation key;
- complete waiter by terminal event;
- cancel waiter by correlation key;
- expire timed-out waiters;
- expose in-flight waiter count for diagnostics/limits.

### 2) Correlation metadata contract

- Correlation key is stable per request (existing `trace_id` baseline).
- `reply_to` identifies response route/channel identity.
- Metadata is persisted in context/service storage and never inferred from payload shape.

### 3) Terminal event contract

Terminal outcomes are explicit and deterministic:

- success terminal payload;
- error terminal payload;
- cancellation terminal payload.

Duplicate terminal events for same correlation key must not produce duplicate responses.

### 4) Cleanup and leak contract

- Timeout always unregisters waiter.
- Cancellation always unregisters waiter.
- Late terminal event after timeout/cancel is handled deterministically (drop + observability).

---

## TDD sequence

### Step A — contract freeze and baseline RED tests

Add RED tests defining mandatory behavior:

- `REPLY-01`: registered waiter receives one terminal success event.
- `REPLY-02`: timeout unregisters waiter and surfaces deterministic timeout outcome.
- `REPLY-03`: duplicate terminal event does not duplicate completion.
- `REPLY-04`: explicit cancel unregisters waiter and yields deterministic cancel outcome.
- `REPLY-05`: late terminal event after timeout/cancel is dropped deterministically.

Detailed Step A execution checklist:

1. Freeze minimal platform contract in code:
   - `TerminalEvent` outcome model (`success|error|cancelled|timeout`);
   - `ReplyWaiterService` protocol (`register/complete/cancel/expire/poll/in_flight`).
2. Add dedicated RED test file for `REPLY-01..05` to avoid mixing with Phase 2/3 transport suites.
3. Run only Phase 4 Step A tests and capture expected RED result.
4. Record RED evidence in this document before Step B implementation.

### Step B — waiter service implementation (GREEN)

Implement in-memory waiter registry service behind framework DI.

Minimum behavior:

- atomic register/complete/cancel semantics;
- no completion after terminal state;
- stable in-flight counter.

### Step C — runtime correlation wiring (GREEN)

Wire correlation through runtime flow:

- ingress path registers waiter and persists reply metadata;
- terminal path resolves waiter by correlation key;
- completion path emits deterministic response outcome object.

### Step D — cross-process boundary activation (GREEN)

Close current gap where supervisor path still runs local callback:

- make terminal events travel through runtime transport boundary contract;
- ensure reply completion path works with process-supervisor profile.

Detailed Step D execution checklist:

1. Introduce explicit boundary execution hook in bootstrap supervisor contract:
   - `execute_boundary(run, run_id, scenario_id, inputs) -> list[Envelope]`
2. Switch process-supervisor runtime path to prefer boundary hook when present.
3. Treat boundary-returned terminal envelopes (`Envelope(payload=TerminalEvent, trace_id=...)`)
   as reply completion signals.
4. Keep deterministic fallback for supervisors without boundary hook (compatibility path).
5. Add focused tests proving:
   - boundary hook is preferred over local runner callback when available;
   - terminal envelopes from boundary complete waiter registry by trace id.

### Step E — timeout/cancel refactor and observability (REFACTOR)

Add deterministic cleanup and instrumentation:

- timeout/cancel counters;
- late-reply drop counters;
- no secret leakage in diagnostic events.

Detailed Step E execution checklist:

1. Add deterministic waiter diagnostics API in in-memory implementation:
   - counters snapshot for lifecycle transitions;
   - bounded sanitized event list.
2. Count timeout/cancel/late-drop paths explicitly:
   - `cancel`;
   - `expire`;
   - `complete` for unknown/late correlation keys.
3. Keep diagnostics payload sanitized:
   - do not expose raw `reply_to`;
   - do not expose raw terminal `payload`;
   - do not expose raw `error`/cancel reason strings.
4. Add focused tests proving:
   - timeout/cancel/late-drop counters are deterministic;
   - diagnostics serialization does not leak secret-like values.

### Step F — regression/parity gate

Run:

- Phase 4 waiter/correlation suite;
- existing Phase 2/3 transport+lifecycle suites;
- CLI parity baseline to ensure no behavior drift for memory profile.

Detailed Step F execution checklist:

1. Run Phase 4 focused suites:
   - reply waiter contract tests;
   - runner reply-correlation tests;
   - process-supervisor boundary correlation slice in builder tests.
2. Run compatibility suites from Phase 2/3 transport+lifecycle tracks.
3. Run deterministic integration scenarios (baseline/experiment + compute_features regression).
4. Run targeted tcp-local reject checks (invalid/replay/oversized guard path).
5. Run CLI parity checks for baseline and experiment configs (`jq -cS` + `diff -u`).
6. Record results in a dedicated Step F report file.

---

## Documentation sync checklist

- Update Phase 4 status in:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- Keep architecture status aligned in:
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)
- Keep runner orchestration expectations aligned in:
  - [Runner loop orchestration](../web/Runner%20loop%20orchestration.md)

---

## Done criteria

- `REPLY-01..05` are green.
- process-supervisor profile can complete request/reply correlation without local callback shortcuts.
- timeout/cancel paths are leak-free and deterministic.
- memory profile regression/parity remains unchanged.

---

## Step A progress note

- [x] Contract freeze artifacts added in platform services:
  - `src/stream_kernel/platform/services/reply_waiter.py`
  - `ReplyWaiterService` protocol
  - `TerminalEvent` model
- [x] RED tests added:
  - `tests/stream_kernel/execution/test_reply_waiter_contract.py`
  - covers `REPLY-01..05`
- [x] RED execution evidence captured (expected failures):
  - command:
    - `.venv/bin/pytest -q tests/stream_kernel/execution/test_reply_waiter_contract.py`
  - result:
    - `5 failed` (`REPLY-01..05`)
    - all failures are `NotImplementedError("Phase 4 Step B is not implemented")`
      from `PendingReplyWaiterService`.

## Step B progress note

- [x] In-memory waiter service implemented:
  - `InMemoryReplyWaiterService` now satisfies `ReplyWaiterService` contract.
  - deterministic terminal behavior added for success/cancel/timeout.
  - duplicate/late terminal events now return `False` and do not overwrite stored terminal outcome.
- [x] GREEN tests:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_reply_waiter_contract.py`
  - result: `5 passed`
- [x] Safety regression for service discovery marker path:
  - `.venv/bin/pytest -q tests/stream_kernel/application_context/test_service_decorator.py -q`
  - result: all passed.

## Step C progress note

- [x] Runtime correlation wiring is implemented:
  - ingress `Envelope(reply_to=...)` path registers waiter in `SyncRunner.run_inputs`;
  - reply metadata is persisted as `__reply_to` in context seed;
  - terminal outcomes (`TerminalEvent`) emitted by nodes complete waiter by trace id.
- [x] Output propagation keeps correlation metadata:
  - downstream envelopes inherit `reply_to` unless explicitly overridden.
- [x] GREEN tests for Step C behavior:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_runner_reply_correlation.py`
  - result: all passed.
- [x] Focused regressions around execution/runtime paths:
  - `.venv/bin/pytest -q tests/stream_kernel/execution`
  - `.venv/bin/pytest -q tests/stream_kernel/app tests/stream_kernel/integration/test_work_queue.py tests/stream_kernel/integration/test_full_flow_dag_runner_integration.py`
  - result: green (with existing skipped markers unchanged).

## Step D progress note

- [x] Bootstrap supervisor contract includes explicit boundary execution API:
  - `execute_boundary(run, run_id, scenario_id, inputs) -> list[Envelope]`
  - local supervisor baseline now uses the same API.
- [x] Process-supervisor lifecycle path now prefers boundary execution hook and
  consumes boundary terminal envelopes to complete reply waiters.
- [x] GREEN tests:
  - `PH4-D-01`: boundary hook is preferred over local runner callback.
  - `PH4-D-02`: boundary terminal envelope completes waiter by trace id.
- [x] Regression suites passed after integration:
  - `.venv/bin/pytest -q tests/stream_kernel/execution tests/stream_kernel/app tests/stream_kernel/integration/test_work_queue.py tests/stream_kernel/integration/test_full_flow_dag_runner_integration.py`

Step D implementation map:

1. Boundary API contract:
   - `src/stream_kernel/platform/services/bootstrap.py`
   - `BootstrapSupervisor.execute_boundary(...)` is now explicit.
2. Lifecycle orchestration wiring:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `execute_with_bootstrap_supervisor(...)` prefers boundary hook when available.
3. Reply completion from boundary terminals:
   - `src/stream_kernel/execution/lifecycle_orchestration.py`
   - `_complete_reply_waiters_from_terminal_outputs(...)` resolves `ReplyWaiterService`
     and completes by `Envelope.trace_id` for `TerminalEvent` payloads.
4. Builder data flow:
   - `src/stream_kernel/execution/builder.py`
   - process-supervisor path forwards `run_id` and ingress `inputs` into boundary execution.
5. TDD coverage:
   - `tests/stream_kernel/execution/test_builder.py`
   - `PH4-D-01` (boundary preferred) and `PH4-D-02` (boundary terminal completes waiter).

## Step E progress note

- [x] Waiter instrumentation is implemented in platform service:
  - `InMemoryReplyWaiterService.diagnostics_counters()`
  - `InMemoryReplyWaiterService.diagnostic_events()`
- [x] Timeout/cancel/late-drop counters are tracked explicitly:
  - `registered`, `completed`, `cancelled`, `expired`,
  - `duplicate_terminal`, `late_reply_drop`,
  - plus runtime snapshot field `in_flight`.
- [x] Diagnostics are sanitized:
  - event stream intentionally excludes raw `reply_to`, terminal payloads, and raw reason/error strings.
- [x] GREEN tests:
  - `REPLY-06`: counters track timeout/cancel/late-drop transitions.
  - `REPLY-07`: diagnostics event stream does not leak secret values.
- [x] Regression checks after Step E:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_reply_waiter_contract.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_runner_reply_correlation.py tests/stream_kernel/execution/test_builder.py -k 'reply or PH4-D or process_supervisor'`

Step E implementation map:

1. Waiter instrumentation:
   - `src/stream_kernel/platform/services/reply_waiter.py`
   - counters + bounded diagnostics events storage.
2. Sanitization policy:
   - `src/stream_kernel/platform/services/reply_waiter.py`
   - diagnostic events include only structural metadata (`kind`, `trace_id`, `terminal_status`, timestamp).
3. TDD coverage:
   - `tests/stream_kernel/execution/test_reply_waiter_contract.py`
   - `REPLY-06` and `REPLY-07`.

## Step F progress note

- [x] Phase 4 focused correlation suites are green.
- [x] Phase 2/3 transport+lifecycle compatibility suites are green.
- [x] Deterministic integration suites are green.
- [x] Tcp-local reject checks are green.
- [x] Memory-profile CLI parity is confirmed for baseline and experiment scenarios.
- [x] Detailed execution evidence is captured in:
  - [web_phase4_stepf_regression_parity_report](web_phase4_stepf_regression_parity_report.md)
