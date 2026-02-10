# Tracing runtime (newgen)

This document defines how **runtime tracing** is configured and wired in the newgen
framework runtime. It describes config structure, CLI overrides, supported sink
adapters, and how tracing relates to other observability streams.

---

## 1. Goals

- Enable deterministic, per-step tracing for debugging and flow analysis.
- Keep tracing configuration **purely declarative** in config.
- Allow CLI overrides for quick enable/disable and trace path changes.
- Keep tracing infrastructure in the framework (not in project/domain ports).
- Keep observability transport on standard framework port types:
  - tracing: `kv_stream`
  - logging: `stream`
  - telemetry: `stream`

---

## 2. Configuration structure

Tracing lives under `runtime.tracing` in the newgen config:

```yaml
runtime:
  tracing:
    enabled: true
    signature:
      mode: type_only
    context_diff:
      mode: whitelist
      whitelist:
        - run_id
        - scenario_id
    sink:
      name: trace_jsonl
      settings:
        path: trace.jsonl
        write_mode: line
        flush_every_n: 1
        fsync_every_n: null
```

### 2.1 Fields

- `enabled` (bool): master switch for tracing.
- `signature.mode` (str): forwarded to `TraceRecorder` (`type_only` recommended).
- `context_diff.mode` (str): `none` | `whitelist` | `full` (see trace spec).
- `context_diff.whitelist` (list[str]): fields to include when mode = `whitelist`.
- `sink.name` (str): framework adapter name, e.g. `trace_jsonl`, `trace_stdout`.
- `sink.settings` (mapping): adapter settings forwarded to the sink adapter factory.

If `sink` is omitted, tracing still records to in-memory `Context.trace` but does
not emit to an external sink.

---

## 3. CLI overrides

The framework CLI allows trace overrides without changing the config file:

- `--tracing enable|disable`
- `--trace-path <path>`

Override rules:
- `--tracing enable` forces `runtime.tracing.enabled = true`.
- `--trace-path` implies tracing enabled (even without `--tracing enable`).
- `--trace-path` forces `runtime.tracing.sink.name = trace_jsonl` and writes into `runtime.tracing.sink.settings.path`.

---

## 4. Runtime wiring

At runtime, the framework:

1) Reads `runtime.tracing`.
2) Loads framework observability modules through extension providers
   (`stream_kernel.observability.discovery_modules()`).
3) Resolves tracing sink as a framework adapter via the same
   discovery/registry path as other adapters.
4) Builds `TraceRecorder` from `signature` + `context_diff`.
5) Builds runtime observers from discovery and binds them via platform
   `FanoutObservabilityService` (no execution shim layer).

Implementation:
- Runtime wiring: `src/stream_kernel/app/runtime.py`
- Extension discovery: `src/stream_kernel/app/extensions.py`
- Execution engine: `src/stream_kernel/execution/runner.py`
- Runtime artifact builder + observability binding: `src/stream_kernel/execution/builder.py`
- Tracing observer: `src/stream_kernel/observability/observers/tracing.py`
- Trace sink adapter discovery/factories: `src/stream_kernel/observability/adapters/tracing.py`
- Trace sink adapters: `src/stream_kernel/adapters/trace_sinks.py`
- Platform observability service: `src/stream_kernel/platform/services/observability.py`
- Observability domain models: `src/stream_kernel/observability/domain/`

---

## 4.1 Tracing scope rules

Tracing is attached at the **runner execution lifecycle** level, so span boundaries
follow runner calls:

- if an adapter participates in DAG execution as a node target, it is traced as a normal node span;
- if an adapter is used as an injected dependency inside a node body, it does not create a separate span by default;
- injected adapter effects are represented inside the caller node span unless explicit nested I/O events are added later.

This keeps execution tracing deterministic and avoids duplicate spans for the same
logical step.

---

## 5. Tests

Tracing behavior is covered by framework tests:

- `tests/stream_kernel/app/test_tracing_runtime.py`
- `tests/adapters/test_trace_sinks.py`
- `tests/stream_kernel/adapters/test_observability_adapters.py`
- `tests/stream_kernel/observability/test_models.py`
- `tests/stream_kernel/observability/test_tracing_observer.py`

These tests ensure:
- JSONL sink writes per-step trace lines.
- Disabled tracing does not create a sink file.
- Runner-executed adapter-nodes are traced as normal spans.
- Injected adapters do not create extra node spans.

### 5.1 Test-case matrix (adapter tracing semantics)

`TRC-ADP-01` adapter executed as graph node:
- setup: enqueue message targeting adapter-node `A`, then route to node `B`;
- expected: trace contains spans for `A` and `B` in execution order.

`TRC-ADP-02` adapter injected into node:
- setup: node `B` calls injected adapter in its body, but adapter is not a graph target;
- expected: trace contains only `B` span (no separate adapter-node span).

`TRC-ADP-03` runtime tracing switch:
- setup: same as above with `runtime.tracing.enabled=false`;
- expected: no sink writes and no external trace stream.

`TRC-NET-01` network ingress boundary:
- setup: network ingress adapter emits domain model into queue;
- expected: tracing records ingress boundary event before first business node span.

`TRC-NET-02` network egress boundary:
- setup: business node emits response model consumed by network egress adapter;
- expected: tracing records egress boundary event after producer node span.

---

## 6. Observability streams

Tracing is only one observability stream. In practice these concerns are related
but not identical:

- `tracing`: per-message/per-step causal path (`TraceRecord`, flow debugging)
- `telemetry`: numeric event stream (latency, throughput, queue depth, retries)
- `monitoring`: alert-oriented aggregation (SLO/SLA checks, anomaly detection)
- `logging`: textual/structured operational logs for operators

The framework keeps these streams separable so teams can route them to different
backends without changing business nodes.

Framework adapters currently exposed for these streams:
- tracing: `trace_stdout`, `trace_jsonl`
- logging: `log_stdout`
- telemetry: `telemetry_stdout`

---

## 7. Notes & future extensions

Potential industrial sources for tracing/observability configuration:
- environment variables
- Kubernetes ConfigMaps / Helm values
- config registry services (Consul/etcd)
- DB-backed configs (Postgres/Redis)

Common production sink targets:
- local console / file (`stdout`, JSONL)
- OpenTelemetry collector (`OTLP gRPC/HTTP`)
- OpenTracing bridge (legacy stacks)
- Kafka / Redpanda topics (observability pipelines)
- log/trace backends (Jaeger, Tempo, Zipkin, Elastic, Loki, Datadog, New Relic)
- metrics backends (Prometheus + remote_write stacks)

The runtime should remain config-driven so these sources can be added without
changing business logic.
