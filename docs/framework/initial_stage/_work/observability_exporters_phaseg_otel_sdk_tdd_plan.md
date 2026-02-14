# Phase G: OpenTelemetry SDK exporter backend (TDD)

## Objective

Integrate official OpenTelemetry SDK tracing pipeline as a backend option (`BatchSpanProcessor` + OTLP exporter).

## Deliverables

- backend bridge that maps framework trace records to OTel SDK spans;
- SDK-managed batch processor lifecycle in platform observability rails;
- configurable OTel exporter type (OTLP HTTP / OTLP gRPC) via SDK settings.

## RED tests

- `OBS-OTELSDK-01` backend creates tracer provider + batch span processor once per runtime.
- `OBS-OTELSDK-02` framework span/resource attributes appear in emitted SDK spans.
- `OBS-OTELSDK-03` shutdown flushes pending spans (`force_flush` + provider shutdown).
- `OBS-OTELSDK-04` exporter failure is isolated and reflected in diagnostics.
- `OBS-OTELSDK-05` context propagation (`trace_id`/`parent_span_id`) remains intact across process hops.

## GREEN target

- backend selectable via `backend=otel_sdk`;
- SDK batching replaces custom per-span network calls for this backend;
- parity with existing Jaeger visualization expectations.

## Refactor

- separate framework trace-model mapping from SDK provider lifecycle wiring.

## Exit criteria

- SDK backend test suite green;
- end-to-end Jaeger runbook updated with SDK profile example.
