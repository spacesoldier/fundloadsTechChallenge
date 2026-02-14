# Phase I: regression/performance matrix sign-off (TDD)

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

## RED tests

- `OBS-MAT-01` each backend/profile combo either passes or fails with documented validation reason.
- `OBS-MAT-02` output parity with reference outputs is preserved for all supported combos.
- `OBS-MAT-03` cross-process span graph continuity preserved in Jaeger for supported combos.
- `OBS-MAT-04` exporter failure injection does not break business pipeline.

## GREEN target

- matrix report generated and committed;
- unsupported combinations explicitly rejected at validation layer;
- chosen default production profile documented.

## Refactor

- remove temporary feature flags/tails introduced during rollout;
- unify diagnostics reporting across all backends.

## Exit criteria

- full matrix regression green (or documented blocked combos);
- performance target met versus current `urllib` per-span baseline.
