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

- [x] Stage 0: Freeze current behavior with characterization tests
  - Add/adjust tests that describe current source/sink bootstrap and adapter contract behavior.
  - Capture current observability lifecycle behavior (before/after/error/run-end).
  - Exit criteria:
    - tests describe behavior we want to keep;
    - tests fail when target behavior is broken.
  - Completed with:
    - explicit characterization for synthetic adapter contract naming (`adapter:<role>`);
    - explicit characterization for builder observability shim binding;
    - explicit runner lifecycle hook characterization (`on_run_end` path);
    - full regression green (`poetry run pytest -q`).

- [x] Stage 1: Remove synthetic `adapter:<role>` contract identity
  - Replace synthetic naming in `build_adapter_contracts` with neutral/stable graph contract ids.
  - Remove prefix-based assumptions from tests and docs.
  - Exit criteria:
    - no `adapter:<role>` contract names in runtime build path;
    - DAG tests validate behavior by consumes/emits contracts, not name prefixes.
  - Completed with:
    - `build_adapter_contracts(...)` now uses adapter role names as contract ids;
    - framework/app builder tests migrated to role-based contract assertions;
    - full regression green (`poetry run pytest -q`).

- [x] Stage 2: Move source bootstrap orchestration out of builder-special flow
  - Replace `build_source_bootstrap_nodes(...)` orchestration path with graph-native source ingress abstraction.
  - Keep one-by-one deterministic source emission and targeted self-reschedule behavior.
  - Exit criteria:
    - builder no longer owns source bootstrap orchestration logic as a special runtime branch;
    - source ingestion remains deterministic and regression tests stay green.
  - Completed with:
    - source ingress orchestration extracted to `stream_kernel.execution.source_ingress`;
    - `build_runtime_artifacts` now consumes `SourceIngressPlan` instead of owning bootstrap construction internals;
    - deterministic source behavior preserved by characterization tests and full regression.

- [x] Stage 3: Remove explicit `input_source` / `output_sink` naming dependency from project config
  - Update test project newgen config roles to neutral names.
  - Keep role resolution discovery-driven from contracts (no hardcoded role strings in framework behavior).
  - Exit criteria:
    - baseline/experiment run with renamed adapter roles;
    - CLI override behavior works through explicit role config or contract-based auto-resolution.
  - Completed with:
    - project newgen configs switched to neutral roles (`ingress_file` / `egress_file`);
    - project I/O adapter contracts renamed accordingly;
    - e2e/runtime/CLI tests updated and green under renamed roles;
    - full regression green (`poetry run pytest -q`).

- [x] Stage 4: Retire step-level sink node from demo project
  - Remove `write_output` business step path and route final outputs to sink adapter contracts directly.
  - Keep output determinism and exact match against reference output.
  - Exit criteria:
    - no project step delegating to file sink (`write_output`) in execution path;
    - e2e baseline/experiment outputs stay identical to reference.
  - Completed with:
    - removed `write_output` from `fund_load.usecases.steps` discovery surface and deleted the step implementation/tests;
    - added runtime-level assertion that build artifacts include `sink:egress_file` and exclude `write_output`;
    - updated e2e sink stubs to consume `OutputLine` directly via graph-native sink contract (`consume(payload)`);
    - full regression green (`poetry run pytest -q`).

- [x] Stage 5: Remove observer shim layer from execution runtime
  - Retire `ObserverBackedObservabilityService` bridge and bind `ObservabilityService` via platform-native discovery/DI.
  - Keep observer lifecycle semantics and trace output behavior.
  - Exit criteria:
    - execution runtime has no adapter shim for observability;
    - observability tests and runtime tracing tests remain green.
  - Completed with:
    - removed `stream_kernel.execution.observability_service` shim module;
    - introduced platform-level `FanoutObservabilityService` in `stream_kernel.platform.services.observability`;
    - switched runtime binding in builder to the platform fan-out service;
    - kept observer lifecycle semantics intact across runner + tracing tests;
    - full regression green (`poetry run pytest -q`).

- [x] Stage 6: Documentation sync and final cleanup
  - Update framework docs and `_work` references for new execution path.
  - Remove dead code/tests left from transitional paths.
  - Exit criteria:
    - docs and code reflect the same runtime model;
    - full regression green.
  - Completed with:
    - synchronized framework docs after Stage 4/5 (`write_output` removal, sink-node model, role-neutral adapter naming);
    - synchronized tracing docs with platform fan-out observability binding (no execution shim);
    - updated roadmap checkboxes for completed graph-native source/sink items;
    - full regression green (`poetry run pytest -q`).

## Notes

- No transitional compatibility shims unless required for an actively failing baseline test.
- Each stage is completed only after:
  - tests added/updated first;
  - implementation follows;
  - full regression run.
