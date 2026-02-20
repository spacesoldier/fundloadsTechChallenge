# Phase I: regression/performance matrix sign-off (TDD)

## Status

- [x] Step A — matrix contract tests (`OBS-MAT-01`) and compatibility map
- [x] Step B — parity regression run (`OBS-MAT-02`)
- [x] Step C — trace continuity regression run (`OBS-MAT-03`)
- [x] Step D — exporter-failure isolation gate (`OBS-MAT-04`)
- [x] Step E — performance characterization snapshot
- [x] Step F — final report and master-plan closure

Report:

- [observability_exporters_phasei_regression_perf_report](observability_exporters_phasei_regression_perf_report.md)

## Objective

Close rollout with full compatibility/performance matrix for all exporter backends and runner profiles.

## Deliverables

- backend/profile compatibility matrix document;
- deterministic regression suite covering sync + multiprocess + async paths;
- baseline latency/throughput snapshots for representative workloads.

## Matrix

Axes:

- Runner: `sync`, `async`
- Topology: `single-process`, `multiprocess`
- Exporter backend:
  - `urllib`
  - `requests`
  - `httpx`
  - `aiohttp`
  - `urllib3`
  - `grpcio`
  - `otel_sdk`

Compatibility policy (validation layer):

- `runner_profile=sync` supports:
  - `urllib`, `requests`, `httpx`, `urllib3`, `grpcio`, `otel_sdk`
- `runner_profile=async` supports:
  - `httpx`, `aiohttp`
- unsupported profile/backend pair must fail with deterministic validation reason
  (or require `settings.bridge=true`).

## RED tests

- `OBS-MAT-01` each backend/profile combo either passes or fails with documented validation reason.
- `OBS-MAT-02` output parity with reference outputs is preserved for all supported combos.
- `OBS-MAT-03` cross-process span graph continuity preserved in Jaeger for supported combos.
- `OBS-MAT-04` exporter failure injection does not break business pipeline.

## GREEN target

- matrix report generated and committed;
- unsupported combinations explicitly rejected at validation layer;
- chosen default production profile documented.

## TDD execution steps

### Step A — matrix contract tests

- Add/extend validator tests to cover full backend/profile matrix:
  - accepted pairs normalize successfully;
  - rejected pairs fail with deterministic message.
- Test ID mapping:
  - `OBS-MAT-01`.

### Step B — deterministic parity regression

- Run focused integration parity suites:
  - baseline end-to-end;
  - experiment end-to-end;
  - execution runtime regression subset.
- Confirm business outputs are unchanged by observability backend profile selection model.
- Test ID mapping:
  - `OBS-MAT-02`.

### Step C — trace continuity regression

- Run tracing regression suites proving parent/child span continuity metadata:
  - route markers (`process_group`, `handoff_from`, `route_hop`);
  - span parent propagation across async/sync boundaries.
- Test ID mapping:
  - `OBS-MAT-03`.

### Step D — exporter failure isolation

- Add/extend regression tests where one sink/exporter fails and pipeline still completes.
- Test ID mapping:
  - `OBS-MAT-04`.

### Step E — performance snapshot

- Characterize current envelope with repeatable local microbench commands.
- Record:
  - throughput;
  - avg latency;
  - p95 latency;
  - environment.

### Step F — sign-off report

- Fill final matrix + commands + outcomes in:
  - `observability_exporters_phasei_regression_perf_report.md`
- Mark blocked/unsupported combos explicitly with validation reason.

## Refactor

- remove temporary feature flags/tails introduced during rollout;
- unify diagnostics reporting across all backends.

## Exit criteria

- full matrix regression green (or documented blocked combos);
- performance target met versus current `urllib` per-span baseline.
