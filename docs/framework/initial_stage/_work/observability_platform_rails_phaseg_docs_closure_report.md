# Phase G Docs/Migration Closure Report

## Scope

Phase G closes documentation migration for observability platform rails:

- remove transitional references to hardcoded sink factory wiring;
- align primary tracing docs with `runtime.observability.pipeline`;
- mark `runtime.tracing` as legacy compatibility path only;
- synchronize architecture references to current execution module layout.

## Updated docs

- `docs/framework/initial_stage/Tracing runtime.md`
- `docs/framework/initial_stage/Execution runtime and routing integration.md`
- `docs/framework/initial_stage/Application context and discovery.md`
- `docs/framework/initial_stage/Factory and injection model.md`
- `docs/framework/initial_stage/Framework boundaries.md`
- `docs/framework/initial_stage/_work/observability_platform_rails_no_hardcode_tdd_plan.md`
- `docs/framework/initial_stage/_work/observability_exporters_and_async_runner_master_tdd_plan.md`
- `docs/framework/initial_stage/_work/_Index_of__work.md`

## Migration closure checks

1. Tracing docs now describe `runtime.observability.pipeline` as primary path.
2. Legacy path is explicit: `runtime.tracing` is compatibility-only.
3. No tracing docs wording suggests sink adapter factory path in runtime hot path.
4. Stale execution references removed in updated architecture docs:
   - `src/stream_kernel/execution/builder.py`
   - `src/stream_kernel/execution/runner.py`
5. `_work` index includes Phase G spec/report artifacts.

## Notes

- Runtime behavior is unchanged in Phase G; this phase only closes documentation drift.
- Legacy compatibility notes are kept intentionally to avoid silent behavioral reinterpretation.
