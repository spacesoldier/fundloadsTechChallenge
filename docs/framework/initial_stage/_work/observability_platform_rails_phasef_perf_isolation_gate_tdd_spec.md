# Observability platform rails Phase F (performance + isolation gate)

## Scope

Freeze two Phase F guarantees for platform observability rails:

1. optional stream fan-out callback failures do not break execution flow;
2. sync baseline keeps lightweight overhead when async pipeline is disabled.

## RED (contract tests)

1. A failing observer in optional fan-out callbacks (`publish_*`, lifecycle hooks) must not
   prevent delivery to other observers.
2. A committed perf/parity report must exist for Phase F with explicit methodology and results.

## GREEN (implementation)

1. `FanoutObservabilityService._fanout_optional(...)` now isolates per-observer failures
   (`try/except` per callback invocation).
2. Added Phase F fan-out isolation test for `publish_trace`.
3. Added committed perf/parity report with measured micro-benchmark numbers.
4. Added report contract test to keep report structure/version controlled.

## Tests

- `tests/stream_kernel/platform/services/test_observability_pipeline_service.py`
  - `test_obs_k_f_01_fanout_isolates_optional_callback_failures`
- `tests/stream_kernel/platform/services/test_observability_phasef_perf_report.py`
  - report presence + required sections gate

## Notes

- Isolation rule is intentionally limited to optional fan-out callbacks to preserve existing
  mandatory lifecycle semantics while keeping observability path failure-tolerant.
