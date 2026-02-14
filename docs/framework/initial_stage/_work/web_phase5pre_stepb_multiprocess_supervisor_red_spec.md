# Web Phase 5pre Step B: multiprocess supervisor contract RED spec

## Goal

Freeze RED-only runtime contracts for a real multiprocess supervisor before Step C
implementation starts.

This step is intentionally test-first:

- no implementation changes;
- explicit failing tests that capture required behavior.

## Why Step B exists

Current `process_supervisor` mode still allows local in-process fallback
(`LocalBootstrapSupervisor`), which does not prove real OS process orchestration.

Step B freezes the missing guarantees:

- process groups are represented by real worker processes;
- worker cardinality (`workers`) is honored;
- startup/readiness/shutdown transitions are observable and deterministic;
- graceful/forced shutdown paths are distinguishable in diagnostics.

## RED contract matrix

### `P5PRE-SUP-01` real process implementation required

- In `process_supervisor` profile, the default DI-resolved
  `BootstrapSupervisor` must not be `LocalBootstrapSupervisor`.
- Runtime should resolve a dedicated multiprocess implementation.

### `P5PRE-SUP-02` startup timeout behavior

- Supervisor startup path must support deterministic timeout behavior.
- When workers do not become ready before timeout, runtime must fail with
  deterministic category (no silent local fallback).

### `P5PRE-SUP-03` graceful stop lifecycle visibility

- Graceful stop path must leave observable lifecycle state indicating
  stop mode = `graceful`.

### `P5PRE-SUP-04` forced stop lifecycle visibility

- Forced terminate fallback path must leave observable lifecycle state indicating
  stop mode = `forced`.

### `P5PRE-SUP-05` workers-per-group honored

- Declared `runtime.platform.process_groups[].workers` must map to concrete
  worker process cardinality for each group.

### `P5PRE-SUP-06` lifecycle logs emitted by supervisor

- Supervisor must emit structured lifecycle events at least for:
  `spawned`, `ready`, `stopping`, `stopped` (and `failed` when relevant).
- Logging must go through platform observability/logging rails.

## Test placement

- `tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`

## Exit criteria for Step B

- RED tests are committed and documented.
- Failure causes are deterministic and map directly to Step C implementation
  tasks.
- No production behavior is changed in this step.

## RED execution evidence

Command:

- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`

Expected failures at current state:

- local fallback still active in discovery (`LocalBootstrapSupervisor`);
- missing `MultiprocessBootstrapSupervisor`;
- missing lifecycle/snapshot/workers-per-group contracts.
