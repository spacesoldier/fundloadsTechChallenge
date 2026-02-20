# Phase H: AsyncRunner rollout (TDD)

## Status

- [x] `RUN-ASYNC-01` implemented in `tests/stream_kernel/execution/runtime/test_async_runner.py`
- [x] `RUN-ASYNC-02` implemented in `tests/stream_kernel/execution/runtime/test_async_runner.py`
- [x] `RUN-ASYNC-03` implemented in `tests/stream_kernel/execution/runtime/test_async_runner.py`
- [x] `RUN-ASYNC-04` implemented in `tests/stream_kernel/execution/orchestration/test_builder.py`
- [x] `RUN-ASYNC-05` covered by validator contract (`OBS-CFG-A-05`)
- [x] `RUN-ASYNC-06` implemented in `tests/stream_kernel/execution/orchestration/test_builder.py`
- [x] `RUN-ASYNC-07` covered by validator contract (`OBS-CFG-A-06`)

Green runs:

- `.venv/bin/pytest -q tests/stream_kernel/execution/runtime`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py`
- `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'obs_cfg_a_05 or obs_cfg_a_06'`

## Objective

Introduce `AsyncRunner` to execute async nodes and async platform services without blocking sync execution rails.

## Deliverables

- `AsyncRunner` contract mirroring `SyncRunner` semantics;
- async queue and routing integration points;
- mixed-mode support strategy (`sync` + `async` process groups);
- observability hooks for async lifecycle (before/after/error/run_end).
- deterministic runner selection policy:
  - runner is chosen by platform from `process_groups[].runner_profile`;
  - node/service dependency contracts must be compatible with selected runner.

## RED tests

- `RUN-ASYNC-01` async node executes via awaitable path and routes outputs correctly.
- `RUN-ASYNC-02` trace continuity preserved across sync->async->sync group boundaries.
- `RUN-ASYNC-03` cancellation/shutdown drains inflight work deterministically.
- `RUN-ASYNC-04` sync profile remains unchanged when async runner is not selected.
- `RUN-ASYNC-05` invalid mixed profile config rejected deterministically.
- `RUN-ASYNC-06` process-group placement chooses runner from `runner_profile` and not from ad-hoc runtime branching.
- `RUN-ASYNC-07` DI-injected async-only dependency in sync group fails during preflight with deterministic validation error.

## GREEN target

- `runner_profile=async` path works with discovery/DI;
- sync and async runners can coexist across process groups;
- observability/exporter services support async flush/close contracts.
- runner selection + dependency compatibility checks are enforced before first payload execution.

## Refactor

- extract common runner core (context seed, envelope normalization, routing result handling);
- keep runner-specific execution primitives isolated (sync call vs await call).

## Exit criteria

- async-focused suites green;
- baseline sync suites unchanged and green.
