# Tracing runtime (newgen)

This document defines how runtime tracing is configured and wired in the
newgen runtime, using platform DI/discovery rails.

---

## 1. Goals

- Keep tracing configuration declarative in runtime config.
- Route exporter activation through framework adapter discovery/registry only.
- Keep tracing as one stream in the unified observability pipeline.
- Preserve strict/non-strict deterministic behavior for missing bindings.

---

## 2. Primary config model

Primary tracing path is under `runtime.observability`.

```yaml
runtime:
  observability:
    pipeline:
      mode: tracing_only
      streams: [tracing]
      strict_bindings: true
      system_nodes:
        - kind: system.obs.trace_dispatch
          enabled: true
          qualifier: observability.trace
    tracing:
      exporters:
        - kind: otel_otlp
          backend: httpx
          settings:
            endpoint: http://127.0.0.1:4318/v1/traces
            service_name: stream-kernel
            batch:
              max_items: 64
              flush_interval_ms: 200
```

### 2.1 Pipeline fields

- `runtime.observability.pipeline.mode`: `tracing_only` or `full_multi_stream`.
- `runtime.observability.pipeline.streams`: enabled streams.
- `runtime.observability.pipeline.strict_bindings`: fail fast on missing exporter
  adapter binding.
- `runtime.observability.pipeline.system_nodes`: optional dispatch system nodes
  (`system.obs.trace_dispatch`, `system.obs.log_dispatch`,
  `system.obs.metric_dispatch`, `system.obs.monitor_dispatch`).

### 2.2 Tracing exporters

`runtime.observability.tracing.exporters[]` supports:

- `kind: jsonl`
- `kind: stdout`
- `kind: otel_otlp` (with `backend` selector)
- `kind: opentracing_bridge`

`otel_otlp` backends currently supported by validator/runtime:

- `urllib`
- `requests`
- `httpx`
- `aiohttp`
- `urllib3`
- `grpcio`
- `otel_sdk`

---

## 3. Legacy compatibility (`runtime.tracing`)

`runtime.tracing` remains a compatibility path for CLI toggles and legacy
single-sink flows.

Compatibility semantics:

- if `runtime.observability.tracing.exporters[]` is configured, tracing observer
  resolves sinks from observability exporters (DI/registry path);
- if exporters are not configured, tracing observer may fall back to
  `runtime.tracing.enabled + runtime.tracing.sink`.

This fallback is legacy-compatible behavior, not the primary architecture path.

---

## 4. CLI overrides

Framework CLI supports:

- `--tracing enable|disable`
- `--trace-path <path>`

Current override behavior writes legacy compatibility keys:

- `runtime.tracing.enabled`
- `runtime.tracing.sink.name=trace_jsonl`
- `runtime.tracing.sink.settings.path=<trace-path>`

When `--trace-path` is used, CLI also ensures adapter settings include
`adapters.trace_jsonl.settings.path`.

---

## 5. Runtime wiring

At runtime the framework:

1. validates `runtime.observability.pipeline` and stream/exporter sections;
2. materializes exporter adapters via `AdapterRegistry` from
   `runtime.observability.<channel>.exporters[]`;
3. builds execution observers from discovery;
4. binds observer fanout through `FanoutObservabilityService`
   (`ObservabilityPipelineService` contract);
5. optionally materializes observability system dispatch nodes from pipeline config.

No runtime hot-path sink factory wiring is used.

Implementation references:

- Runtime wiring entry: `src/stream_kernel/app/runtime.py`
- Runtime artifact builder: `src/stream_kernel/execution/orchestration/builder.py`
- Execution engine: `src/stream_kernel/execution/runtime/runner.py`
- Tracing observer: `src/stream_kernel/observability/observers/tracing.py`
- Observability adapter registration: `src/stream_kernel/observability/adapters/tracing.py`
- Exporter adapters: `src/stream_kernel/adapters/trace_sinks.py`
- Platform observability service: `src/stream_kernel/platform/services/observability.py`

---

## 6. Tracing scope rules

Tracing is attached at runner execution lifecycle boundaries:

- adapter executed as a DAG node target -> traced as a normal node span;
- adapter injected into a node body -> no separate node span by default;
- injected adapter effects are represented inside caller node span unless
  explicit nested events are emitted.

---

## 7. Tests

Representative coverage:

- `tests/stream_kernel/app/test_tracing_runtime.py`
- `tests/adapters/test_trace_sinks.py`
- `tests/stream_kernel/adapters/test_observability_adapters.py`
- `tests/stream_kernel/observability/test_tracing_observer.py`
- `tests/stream_kernel/platform/services/test_observability_pipeline_service.py`

Case matrix (short):

- `TRC-ADP-01`: adapter as graph node -> adapter/node spans present.
- `TRC-ADP-02`: adapter injected into node -> only node span.
- `TRC-ADP-03`: tracing disabled -> no sink writes.
- `TRC-NET-01`: ingress boundary event present.
- `TRC-NET-02`: egress boundary event present.

---

## 8. Stream model

Tracing is one observability stream among:

- tracing (causal path)
- logging (operator events)
- telemetry (numeric runtime signals)
- monitoring (alert-oriented aggregations)

Streams are separable and can be routed to different backends without business
node changes.
