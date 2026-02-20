# Web Phase 5pre Step F: OTel/OpenTracing observability adapters (TDD spec)

## Goal

Add framework-native trace exporter adapters for:

- OpenTelemetry OTLP (Jaeger-compatible via OTel Collector),
- OpenTracing bridge (legacy compatibility mode),

and wire them into execution tracing observer construction without breaking
existing JSONL/stdout behavior.

## Scope

In scope:

- new trace sink adapters:
  - `trace_otel_otlp`
  - `trace_opentracing_bridge`
- observer-factory support for `runtime.observability.tracing.exporters[]`;
- fan-out sink path for multiple trace exporters in one run;
- exporter failure isolation (observer must not break business execution).

Out of scope:

- real network OTLP transport implementation and retries;
- full OpenTracing SDK runtime dependency binding;
- metrics/logs OTLP exporters.

## Runtime contract (Step F)

### New config path used by observer factory

`runtime.observability.tracing.exporters` entries:

- `kind: jsonl | stdout | otel_otlp | opentracing_bridge`
- `settings: mapping`

Step F keeps backward compatibility with legacy path:

- `runtime.tracing.enabled + runtime.tracing.sink`

Priority:

1. if `runtime.observability.tracing.exporters` is non-empty, use this path;
2. else fallback to legacy `runtime.tracing`.

## Adapter contracts

### `trace_otel_otlp`

- consumes: `TraceMessage` (platform trace stream contract),
- binds: `kv_stream`,
- runtime sink interface: `emit(record)`, `flush()`, `close()`,
- maps `TraceRecord` -> OTLP-like span dict payload.

### `trace_opentracing_bridge`

- consumes: `TraceMessage`,
- binds: `kv_stream`,
- runtime sink interface: `emit(record)`, `flush()`, `close()`,
- maps `TraceRecord` -> OpenTracing-like span dict payload (operation/tags/logs).

## Failure-isolation contract

Exporter exceptions must not propagate to runner path:

- failing exporter increments dropped/error diagnostics internally;
- other exporters in fan-out continue receiving spans;
- node execution lifecycle remains successful.

## TDD cases

- `P5PRE-OBS-01` OTel exporter receives span payload with preserved `trace_id`.
- `P5PRE-OBS-02` OpenTracing bridge receives mapped operation/tags.
- `P5PRE-OBS-03` exporter failure is isolated (no exception to runner/observer caller).
- `P5PRE-OBS-04` observer factory builds from `runtime.observability.tracing.exporters`.
- `P5PRE-OBS-05` discovery metadata includes new adapters.

## Files expected

- `src/stream_kernel/adapters/trace_sinks.py`
  - sink classes for OTLP and OpenTracing bridge.
- `src/stream_kernel/observability/adapters/tracing.py`
  - adapter factories `trace_otel_otlp`, `trace_opentracing_bridge`.
- `src/stream_kernel/observability/adapters/__init__.py`
  - export new adapter factories.
- `src/stream_kernel/observability/observers/tracing.py`
  - exporter-list config path and fan-out sink assembly.
- tests:
  - `tests/adapters/test_trace_sinks.py`
  - `tests/stream_kernel/adapters/test_observability_adapters.py`
  - `tests/stream_kernel/observability/test_tracing_observer_factory.py`

## Validation commands

- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py`
- `.venv/bin/pytest -q tests/stream_kernel/adapters/test_observability_adapters.py`
- `.venv/bin/pytest -q tests/stream_kernel/observability/test_tracing_observer_factory.py tests/stream_kernel/observability/test_tracing_observer.py`

