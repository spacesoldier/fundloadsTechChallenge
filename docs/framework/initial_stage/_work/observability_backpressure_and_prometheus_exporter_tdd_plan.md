# Observability backpressure + Prometheus exporter (TDD plan)

## Goal

Close current observability overload gap and make losses measurable/alertable.

Target outcome:

- no silent trace drops under normal load;
- explicit bounded backpressure policy (`block_with_timeout`) on trace queues;
- supervisor-owned metrics export path for Prometheus;
- deterministic shutdown diagnostics proving drain/flush completeness.

---

## Context

Current diagnostics already show useful signals in lifecycle events:

- `dispatch_submit_dropped_total`
- `dispatch_dropped`
- `pending_total_estimate`
- per-sink `exported/dropped/buffered`

But there is no first-class Prometheus metrics exporter yet, and dispatch queue
drop behavior is still primarily lossy under pressure.

---

## Scope

In scope:

- supervisor trace dispatch queue backpressure policy contract;
- sink queue backpressure policy parity;
- unified metrics snapshot service for observability transport internals;
- Prometheus exporter adapter (separate monitoring exporter);
- `http_pull` and `textfile` modes;
- tests + runbook updates.

Out of scope (separate track):

- distributed queue/broker migration (Redis/Kafka);
- cross-host metrics federation;
- adaptive auto-tuning with control loops.

---

## Architecture constraints

- platform rails only (ports/services/adapters), no ad-hoc side channels;
- workers remain focused on business execution; supervisor owns observability sinks in multiprocess mode;
- no recursive self-observation loops (`system.obs.*` guard remains mandatory);
- strict mode must fail fast on invalid monitoring/backpressure configs.

---

## Config contract (planned)

### 1) Trace dispatch queue policy (supervisor)

```yaml
runtime:
  observability:
    tracing:
      dispatch_queue:
        max_items: 32768
        drop_policy: block_with_timeout   # drop_newest|drop_oldest|block_with_timeout
        block_timeout_ms: 100
```

### 2) OTLP sink queue policy

```yaml
runtime:
  observability:
    tracing:
      exporters:
        - kind: otel_otlp
          settings:
            transport:
              queue:
                max_items: 10000
                drop_policy: block_with_timeout
                block_timeout_ms: 100
```

### 3) Prometheus monitoring exporter

```yaml
runtime:
  observability:
    monitoring:
      exporters:
        - kind: prometheus
          enabled: true
          settings:
            mode: http_pull               # http_pull|textfile
            namespace: stream_kernel
            subsystem: observability
            http:
              host: 127.0.0.1
              port: 9464
              path: /metrics
            textfile:
              path: metrics/stream_kernel.prom
```

---

## Phase A — Contract freeze and validator RED

Status (2026-02-19): completed.

### A1 RED

Add failing validator tests:

- rejects unknown `dispatch_queue.drop_policy`;
- requires positive `dispatch_queue.max_items`;
- requires positive `block_timeout_ms` for `block_with_timeout`;
- validates `monitoring.exporters[].kind=prometheus`;
- validates `prometheus.settings.mode` and mode-specific blocks (`http`, `textfile`).

### A2 GREEN

Implement validator support and normalization:

- `runtime.observability.tracing.dispatch_queue.*`
- `runtime.observability.monitoring.exporters[]` with `kind=prometheus`

### A3 REFACTOR

- keep grouped exporter settings style consistent (`settings.transport.*`, `settings.http.*`).
- keep `pipeline` exclusivity checks aligned across tracing/logging/monitoring exporters.

---

## Phase B — Dispatch queue backpressure behavior

Status (2026-02-19): completed.

### B1 RED

Add runtime tests:

- submit blocks until free slot under `block_with_timeout`;
- timeout increments `submit_timeout` and deterministic drop metric;
- `drop_newest` and `drop_oldest` behavior remains deterministic and covered.

### B2 GREEN

Update `AsyncDispatchLoop`:

- policy-aware submit path (`drop_newest`, `drop_oldest`, `block_with_timeout`);
- explicit timeout handling;
- metrics:
  - `submit_block_count`
  - `submit_timeout_count`
  - `submit_block_wait_ms_total`

Wire policy from supervisor tracing config.

### B3 REFACTOR

- keep lock/queue interactions minimal and deterministic;
- preserve existing `metrics()` compatibility.

---

## Phase C — Sink queue backpressure parity

Status (2026-02-19): completed.

### C1 RED

Add sink tests:

- OTLP sink honors `block_with_timeout` semantics;
- timeout path increments sink drop/timeout diagnostics;
- `flush/close` keeps deterministic exported counters.

### C2 GREEN

Implement policy parity in `OTelOtlpTraceSink` queue admission:

- support `block_with_timeout` with bounded wait;
- expose additional diagnostics:
  - `submit_timeout_total`
  - `block_wait_ms_total`

### C3 REFACTOR

- avoid hot-path overhead for non-blocking policies;
- keep async/native backend behavior unchanged.

---

## Phase D — Metrics snapshot service

Status (2026-02-19): completed.

### D1 RED

Add tests for normalized metrics snapshot:

- combines dispatch metrics + sink diagnostics + loss estimate;
- stable key names and labels;
- includes lifecycle stage markers (`stop_requested`, `close_begin`, `close_end`).

### D2 GREEN

Introduce a platform service (for example `ObservabilityMetricsService`) that:

- ingests supervisor diagnostics snapshots;
- keeps latest gauges + monotonic counters;
- serves exporter-friendly metric records.

### D3 REFACTOR

- isolate metric naming/labeling rules from exporter format.

---

## Phase E — Prometheus exporter adapter

Status (2026-02-19): completed.

### E1 RED

Add adapter tests:

- `prometheus` exporter emits valid exposition format;
- `http_pull` mode exposes `/metrics`;
- `textfile` mode writes deterministic `.prom` file;
- exporter failure does not break business execution.

### E2 GREEN

Implement separate monitoring exporter adapter:

- mode `http_pull`: lightweight in-process endpoint;
- mode `textfile`: periodic/snapshot write for batch runs;
- integrates with `ObservabilityMetricsService` snapshot.
- keeps execution on the established async rails (supervisor-side dispatch + non-fatal exporter failure isolation).

### E3 REFACTOR

- add shared rendering helpers for counters/gauges/histograms.

---

## Phase F — Multiprocess integration and graceful stop

Status (2026-02-19): completed.

### F1 RED

Integration tests:

- multiprocess run exposes non-zero dispatch/sink metrics;
- stop sequence publishes final drained snapshot (`pending=0`) when no loss;
- synthetic overload run produces alertable drop metrics.

### F2 GREEN

Supervisor integration:

- register Prometheus exporter as monitoring exporter;
- publish metrics snapshots at runtime and on stop boundaries;
- ensure exporter close happens after final metrics flush.
- run metrics aggregation through `system.obs.monitoring_metrics_dispatch` (node -> service)
  while keeping exporters colocated with supervisor process for now.
- verify multiprocess smoke path:
  - non-zero dispatch/sink counters are exported;
  - final `close_end` snapshot reports drained `pending_total_estimate=0`;
  - overload diagnostics produce explicit loss metrics (`loss_estimate_total` > 0).

### F3 REFACTOR

- align lifecycle logging and metrics timestamps for correlation.

---

## Metric catalog (target)

Core:

- `stream_kernel_trace_dispatch_submitted_total`
- `stream_kernel_trace_dispatch_processed_total`
- `stream_kernel_trace_dispatch_failed_total`
- `stream_kernel_trace_dispatch_dropped_total`
- `stream_kernel_trace_dispatch_submit_dropped_total`
- `stream_kernel_trace_dispatch_queue_depth`
- `stream_kernel_trace_dispatch_pending`
- `stream_kernel_trace_dispatch_submit_block_count_total`
- `stream_kernel_trace_dispatch_submit_timeout_total`
- `stream_kernel_trace_dispatch_submit_block_wait_ms_total`

Sink-level:

- `stream_kernel_trace_sink_exported_total{sink_kind,sink_index}`
- `stream_kernel_trace_sink_dropped_total{sink_kind,sink_index}`
- `stream_kernel_trace_sink_buffered{sink_kind,sink_index}`
- `stream_kernel_trace_sink_pending_estimate{sink_kind,sink_index}`

Aggregate:

- `stream_kernel_trace_pending_total_estimate`
- `stream_kernel_trace_loss_estimate_total`

OTLP transport (optional in first cut):

- `stream_kernel_otlp_export_requests_total{backend,outcome}`
- `stream_kernel_otlp_export_duration_seconds{backend}`
- `stream_kernel_otlp_export_batch_items{backend}`
- `stream_kernel_otlp_export_payload_bytes{backend}`
- `stream_kernel_otlp_export_retries_total{backend}`

---

## Acceptance criteria

1. Under baseline load, drop counters stay at zero.
2. Under synthetic overload, drops (or backpressure timeouts) are explicit and measurable.
3. No undocumented span losses: every missing-span incident is explainable via
   filtering rules or metrics.
4. Prometheus scrape provides stable metric names and labels.
5. Business output parity remains unchanged.

---

## Rollout sequence

1. Phase A (validator/contract)
2. Phase B (dispatch queue policy)
3. Phase C (sink queue policy)
4. Phase D (metrics snapshot service)
5. Phase E (Prometheus exporter)
6. Phase F (multiprocess integration + graceful-stop closure)

---

## Linked docs

- `docs/framework/initial_stage/Tracing runtime.md`
- `docs/framework/initial_stage/web/analysis/Observability backpressure and Prometheus metrics model.md`
- `docs/framework/initial_stage/_work/runtime_nonblocking_and_graceful_stop_audit_plan.md`
- `docs/framework/initial_stage/_work/observability_exporters_and_async_runner_master_tdd_plan.md`
