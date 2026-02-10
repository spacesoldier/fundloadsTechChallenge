# Graph-native remaining cleanup plan

## Goal

Close remaining non-native execution paths so runtime is fully discovery/DI-driven:

- no synthetic `adapter:<role>` contract names;
- no builder-only bootstrap orchestration as a special case;
- no project hardcoded `input_source` / `output_sink` naming dependency;
- no step-level output sink (`write_output`) when sink adapters can consume graph outputs directly;
- no observer shim layer in execution path.

## Baseline

- Keep full regression green before each stage:
  - `poetry run pytest -q`
  - `tests/integration`
  - `tests/stream_kernel`

## Stages (TDD-first)

- [ ] Stage 0: Freeze current behavior with characterization tests
  - Add/adjust tests that describe current source/sink bootstrap and adapter contract behavior.
  - Capture current observability lifecycle behavior (before/after/error/run-end).
  - Exit criteria:
    - tests describe behavior we want to keep;
    - tests fail when target behavior is broken.

- [ ] Stage 1: Remove synthetic `adapter:<role>` contract identity
  - Replace synthetic naming in `build_adapter_contracts` with neutral/stable graph contract ids.
  - Remove prefix-based assumptions from tests and docs.
  - Exit criteria:
    - no `adapter:<role>` contract names in runtime build path;
    - DAG tests validate behavior by consumes/emits contracts, not name prefixes.

- [ ] Stage 2: Move source bootstrap orchestration out of builder-special flow
  - Replace `build_source_bootstrap_nodes(...)` orchestration path with graph-native source ingress abstraction.
  - Keep one-by-one deterministic source emission and targeted self-reschedule behavior.
  - Exit criteria:
    - builder no longer owns source bootstrap orchestration logic as a special runtime branch;
    - source ingestion remains deterministic and regression tests stay green.

- [ ] Stage 3: Remove explicit `input_source` / `output_sink` naming dependency from project config
  - Update test project newgen config roles to neutral names.
  - Keep role resolution discovery-driven from contracts (no hardcoded role strings in framework behavior).
  - Exit criteria:
    - baseline/experiment run with renamed adapter roles;
    - CLI override behavior works through explicit role config or contract-based auto-resolution.

- [ ] Stage 4: Retire step-level sink node from demo project
  - Remove `write_output` business step path and route final outputs to sink adapter contracts directly.
  - Keep output determinism and exact match against reference output.
  - Exit criteria:
    - no project step delegating to file sink (`write_output`) in execution path;
    - e2e baseline/experiment outputs stay identical to reference.

- [ ] Stage 5: Remove observer shim layer from execution runtime
  - Retire `ObserverBackedObservabilityService` bridge and bind `ObservabilityService` via platform-native discovery/DI.
  - Keep observer lifecycle semantics and trace output behavior.
  - Exit criteria:
    - execution runtime has no adapter shim for observability;
    - observability tests and runtime tracing tests remain green.

- [ ] Stage 6: Documentation sync and final cleanup
  - Update framework docs and `_work` references for new execution path.
  - Remove dead code/tests left from transitional paths.
  - Exit criteria:
    - docs and code reflect the same runtime model;
    - full regression green.

## Notes

- No transitional compatibility shims unless required for an actively failing baseline test.
- Each stage is completed only after:
  - tests added/updated first;
  - implementation follows;
  - full regression run.
