# Observability platform rails Phase G (documentation and migration closure)

## Scope

Close remaining documentation migration gaps after Phase F:

1. remove transitional wording that implies runtime hot-path sink factory wiring;
2. align architecture references with current execution module layout;
3. keep legacy tracing fallback explicitly marked as compatibility-only.

## RED (contract checks)

1. No committed Phase G closure report exists.
2. `Tracing runtime` doc still describes only `runtime.tracing` as primary.
3. `Tracing runtime` doc still mentions sink adapter factory wording and stale
   execution module paths (`execution/builder.py`, `execution/runner.py`).

## GREEN (implementation)

1. Add committed migration closure report with explicit checklist and updated-doc list.
2. Rewrite tracing runtime architecture section around
   `runtime.observability.pipeline` + exporter model.
3. Keep `runtime.tracing` described only as legacy compatibility path.
4. Synchronize architecture docs with current modules:
   - `src/stream_kernel/execution/orchestration/builder.py`
   - `src/stream_kernel/execution/runtime/runner.py`
   - `src/stream_kernel/routing/routing_service.py`
5. Synchronize `_work` index and plan statuses.

## Tests

- `tests/stream_kernel/platform/services/test_observability_phaseg_docs_report.py`
  - `test_obs_k_g_01_docs_closure_report_is_committed_with_required_sections`
  - `test_obs_k_g_02_tracing_doc_marks_legacy_path_and_removes_factory_wording`

## Notes

- Phase G is documentation-only closure; no runtime behavior change is intended.
- Legacy fallback remains documented for CLI compatibility and migration safety.
