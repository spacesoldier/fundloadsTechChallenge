# Web Phase 5pre Step E: boundary delegation to multiprocess workers (TDD spec)

## Goal

Complete the missing execution handoff path for `bootstrap.mode=process_supervisor`:

- parent runtime dispatches boundary batches to real worker processes;
- workers execute boundary targets through child bootstrap runtime;
- parent receives `RoutingResult.terminal_outputs` and keeps existing reply-correlation flow.

This step closes the main functional gap between Step D (worker lifecycle) and the
future FastAPI/web execution split.

## Scope

In scope:

- `MultiprocessBootstrapSupervisor.execute_boundary(...)` implementation;
- per-worker control channel for boundary commands/results;
- deterministic worker selection for each dispatch group;
- deterministic error categories for missing/unavailable workers and child failures;
- preservation of `trace_id` and `reply_to` in terminal outputs.

Out of scope:

- heartbeat/restart policy (covered in later steps);
- async fan-out/fan-in optimization across workers;
- web endpoint integration.

## Parent-worker boundary contract

### Parent -> worker command

`execute_boundary` command fields:

- `correlation_id: str`
- `run_id: str`
- `scenario_id: str`
- `inputs: list[BoundaryDispatchInput]` (already grouped by `dispatch_group`)

### Worker -> parent response

Success:

- `kind=execute_boundary_result`
- `correlation_id`
- `terminal_outputs: list[Envelope]`

Failure:

- `kind=execute_boundary_error`
- `correlation_id`
- `category: timeout | transport | execution`
- `message: str` (deterministic, sanitized)

## Execution model

1. `start_groups(...)` spawns workers and creates one duplex control channel per worker.
2. `execute_boundary(...)` groups incoming boundary inputs by `dispatch_group`.
3. For each group:
   - select one alive worker (round-robin within group);
   - send command with grouped inputs;
   - wait for response with bounded timeout;
   - append returned terminal outputs.
4. Return consolidated `RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[...])`.

## Deterministic failure mapping

- no known worker group / no alive worker / broken channel:
  - raise `ConnectionError("remote handoff transport failed ...")` category path.
- worker response timeout:
  - raise `TimeoutError("remote handoff timed out ...")` category path.
- worker-side execution error:
  - raise `RuntimeError("remote handoff failed ...")` category path.

`lifecycle_orchestration._map_worker_failure(...)` keeps final message/category mapping
without leaking raw child details.

## TDD cases for Step E

- `P5PRE-EXEC-01`:
  cross-group workload is executed in target worker process and returns terminal output.
- `P5PRE-EXEC-02`:
  missing/unknown dispatch group raises deterministic transport failure category.
- `P5PRE-EXEC-03`:
  terminal outputs preserve `trace_id` and `reply_to` through boundary round-trip.

(`P5PRE-EXEC-04` readiness gating remains covered by existing lifecycle/start-order tests in builder/runtime orchestration.)

## Files expected in this step

- `src/stream_kernel/platform/services/bootstrap.py`
  - worker control channel state;
  - boundary command processing in worker loop;
  - execute/decode/aggregate path in supervisor.
- `tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
  - new Step E contract tests.
- `docs/framework/initial_stage/_work/web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md`
  - Step E status update.

## Validation commands

- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_remote_handoff_contract.py`

