# Observability/Execution Phase J — DI-driven runner auto-selection (TDD plan)

## Goal

Replace hardcoded runtime selection by `process_groups[].runner_profile` with framework-owned automatic runner selection based on dependency injection metadata, while keeping explicit override for exceptional cases.

Target behavior:

- default path: runner is inferred automatically from discovered node dependencies;
- override path: `process_groups[].runner_profile` is optional and applied only when explicitly provided;
- preflight/runtime rejects incompatible explicit override + async/sync dependency contracts;
- queue qualifier resolution remains deterministic.

## Why this phase now

Current implementation chooses runner from `process_groups[].runner_profile` even when preflight already has dependency metadata. This creates configuration burden and does not match platform rails where execution mode should be inferred from dependency contracts.

## Scope

In scope:

- execution planning updates (`plan_pools`) with qualifier-aware async binding detection;
- runtime runner selection updates in orchestration builder;
- config validation updates to make `runner_profile` optional (no implicit default materialization);
- compatibility checks to use explicit overrides only;
- regression coverage for sync/async auto-path and override path.

Out of scope:

- per-node mixed runner execution inside one process group;
- scheduler-level split of sync/async subgraphs to different worker pools;
- OTLP backend auto-selection by runner profile (tracked in next tasks).

## Design decisions

1. Source of truth for auto-selection

- Use DI metadata (`InjectionRegistry.is_async_binding(...)`) resolved against node `@inject` markers.
- Selection rule for current single-runner execution unit:
  - if at least one executable node requires async dependency binding -> choose `async`;
  - otherwise choose `sync`.

2. Explicit override policy

- `runner_profile` remains supported but optional.
- Override is used only when key is explicitly present in selected process group.
- Explicit override mismatch with dependency requirements must fail fast.

3. Compatibility checks

- Config-time checks that rely on runner profile (e.g., observability backend matrix) use only explicit profiles.
- If no explicit profile exists, checks defer to runtime auto-selection and should not produce false negatives.

## TDD steps

### Step A — RED: planning contract coverage

Add/extend tests for:

- qualifier-aware async detection in `plan_pools`;
- bound-method/container node markers in planning scan;
- missing binding is non-fatal for planning (treated as sync fallback).

### Step B — RED: runtime auto-selection contract

Add tests in builder/runtime orchestration:

- selects `AsyncRunner` when DI plan has async dependency and no explicit profile;
- selects `SyncRunner` when DI plan is sync-only and no explicit profile;
- explicit `runner_profile` override still takes precedence.

### Step C — GREEN: implement planner/builder changes

Implement:

- qualifier-aware and container-aware injected-field scan in `execution/runtime/planning.py`;
- new builder resolver for runner profile:
  - explicit override from selected group when provided;
  - otherwise infer from `scenario.steps + injection_registry`.

### Step D — RED/GREEN: validator behavior

Update tests and validator so that:

- `runner_profile` is optional in `runtime.platform.process_groups`;
- validator does not silently inject default `sync`;
- observability backend/profile compatibility checks evaluate explicit profiles only.

### Step E — regression pass

Run targeted regression suites:

- `tests/stream_kernel/execution/runtime/test_execution_planning.py`
- `tests/stream_kernel/execution/orchestration/test_builder.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/execution/runtime` (smoke)

### Step F — docs sync and sign-off

- update master plan status (`Phase J` complete);
- document resulting runner selection contract in phase doc and master plan.

## Execution status

- [x] Step A complete
- [x] Step B complete
- [x] Step C complete
- [x] Step D complete
- [x] Step E complete
- [x] Step F complete

## Regression commands run

- `.venv/bin/pytest -q tests/stream_kernel/execution/runtime/test_execution_planning.py tests/stream_kernel/execution/orchestration/test_builder.py tests/stream_kernel/config/test_newgen_validator.py -k 'plan_pools or RUN-AUTO or runner_profile or obs_cfg_a_05b or execute_runtime_artifacts_selects_async_runner_from_injected_dependencies or explicit_runner_profile_overrides_auto_selection'`
- `.venv/bin/pytest -q tests/stream_kernel/execution/runtime/test_execution_planning.py tests/stream_kernel/execution/orchestration/test_builder.py tests/stream_kernel/config/test_newgen_validator.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/runtime`

## Acceptance criteria

- no required `runner_profile` for normal operation;
- async-capable DI dependencies auto-switch execution to `AsyncRunner`;
- explicit override works and is validated;
- no false validation failure for observability backends when profile is not explicitly fixed;
- all listed tests pass.

## Next immediate follow-ups

- OTLP backend runtime selection by inferred runner (`httpx`/`aiohttp` path selection);
- batch tuning defaults (`batch.max_items`, `batch.flush_interval_ms`) using measured profiles.
