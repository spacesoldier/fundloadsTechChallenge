# Observability platform rails Phase E (dependency groups + backend availability diagnostics)

## Scope

Close startup-time dependency diagnostics gap for observability exporters and freeze optional dependency groups in Poetry.

## RED (contract tests)

1. If OTLP backend dependency is missing and no explicit degrade mode is configured, startup must fail deterministically.
2. If explicit degrade mode is configured, startup must remain deterministic and continue with no-op exporter behavior.
3. Validator must reject unsupported dependency-missing mode values.
4. Validator must preserve accepted mode values in normalized config.
5. Runtime adapter build error must include deterministic failure detail for diagnostics.

## GREEN (implementation)

1. Added eager dependency probe API in trace sinks:
   - `check_otel_backend_dependencies(backend)`
2. Added degraded fallback sink:
   - `NoOpTraceSink`
3. Updated `trace_otel_otlp` adapter:
   - supports `settings.dependency_missing` with values:
     - `error` (default)
     - `degrade_noop`
   - performs startup dependency check and either fails fast or degrades to no-op.
4. Validator normalization updated:
   - `runtime.observability.tracing.exporters[*].settings.dependency_missing`
5. Runtime observability adapter builder error now includes cause detail.
6. Poetry optional groups added:
   - `observability-http`
   - `observability-async-http`
   - `observability-grpc`
   - `observability-otel-sdk`
   - `observability-all`

## Tests

- `tests/stream_kernel/adapters/test_observability_adapters.py`
  - startup fail on missing dependency by default
  - explicit `degrade_noop` fallback
- `tests/stream_kernel/config/test_newgen_validator.py`
  - reject invalid `dependency_missing`
  - accept/normalize `degrade_noop`
- `tests/stream_kernel/execution/orchestration/test_builder.py`
  - deterministic build error contains dependency detail
  - explicit degrade mode stays non-fatal in strict path

## Notes

- This phase keeps transport-library specifics inside adapter boundaries.
- Startup diagnostics now surface missing dependency deterministically without waiting for first trace export call.
