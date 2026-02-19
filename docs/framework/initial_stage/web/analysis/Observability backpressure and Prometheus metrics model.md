# Observability backpressure and Prometheus metrics model

## Goal

Define an operational model for observability overload handling:

- detect where trace events are dropped;
- separate slice-filtering effects from real transport losses;
- provide Prometheus-ready metrics contract for alerting and tuning.

This document is an analysis/specification reference for implementation plans.

---

## 1) Problem statement

In multiprocess mode, trace delivery is not a single queue.
A record can be delayed or dropped at multiple layers:

1. worker -> supervisor control channel;
2. supervisor trace dispatch queue;
3. sink-local buffers (JSONL/OTLP);
4. exporter network path (collector unavailable, retries exhausted).

Observed symptom pattern:

- traces in Jaeger look incomplete;
- close-time diagnostics may show `pending_total_estimate=0`;
- yet drop counters (`dispatch_submit_dropped_total`) are non-zero.

Conclusion: losses can happen during steady-state overload even when shutdown is clean.

---

## 2) Queue topology model

Current topology (supervisor-owned observability sinks):

```
worker_trace -> supervisor ingest -> dispatch queue -> sink emit -> sink batch -> exporter transport
```

Pressure points:

- `Q1`: supervisor dispatch queue (`AsyncDispatchLoop`)
- `Q2`: sink batch queue/buffer (`OTelOtlpTraceSink` span buffer)

Backpressure objective:

- prefer controlled blocking (`block_with_timeout`) over silent drops;
- keep bounded memory and bounded stop latency;
- avoid deadlock when collector is unavailable.

---

## 3) Loss taxonomy

### 3.1 Not a loss: view/slice filtering

Filtering by design:

- `trace_view=logical|topology`;
- `trace_slice=business_logic|platform_internals`;
- parent relinking in filtered views (`stream_kernel.parent_resolution=*`).

These reduce visible spans but do not imply transport loss.

### 3.2 Runtime drops

Detected by counters:

- dispatch submit drops (`dispatch_submit_dropped_total`);
- dispatch worker drops (`dispatch_dropped`);
- sink drops (`sink_diagnostics[].dropped`).

### 3.3 Shutdown truncation

Detected by pending at close:

- `pending_total_estimate > 0` at `stage=close_end`.

If pending is zero and drops are non-zero, losses happened before shutdown.

---

## 4) Metrics contract (Prometheus-oriented)

Metrics are supervisor-owned in multiprocess mode.

### 4.1 Dispatch queue metrics

- `stream_kernel_trace_dispatch_submitted_total`
- `stream_kernel_trace_dispatch_processed_total`
- `stream_kernel_trace_dispatch_failed_total`
- `stream_kernel_trace_dispatch_dropped_total`
- `stream_kernel_trace_dispatch_submit_dropped_total`
- `stream_kernel_trace_dispatch_queue_depth` (gauge)
- `stream_kernel_trace_dispatch_pending` (gauge)

### 4.2 Sink metrics

- `stream_kernel_trace_sink_exported_total{sink_kind,sink_index}`
- `stream_kernel_trace_sink_dropped_total{sink_kind,sink_index}`
- `stream_kernel_trace_sink_buffered{sink_kind,sink_index}` (gauge)
- `stream_kernel_trace_sink_pending_estimate{sink_kind,sink_index}` (gauge)

### 4.3 Aggregate integrity metrics

- `stream_kernel_trace_pending_total_estimate` (gauge)
- `stream_kernel_trace_loss_estimate_total` (counter-like monotonic projection):
  `dispatch_submit_dropped_total + dispatch_dropped + sum(sink_dropped_total)`

### 4.4 OTLP transport metrics (optional but recommended)

- `stream_kernel_otlp_export_requests_total{backend,outcome}`
- `stream_kernel_otlp_export_duration_seconds{backend}` (histogram)
- `stream_kernel_otlp_export_batch_items{backend}` (histogram)
- `stream_kernel_otlp_export_payload_bytes{backend}` (histogram)
- `stream_kernel_otlp_export_retries_total{backend}`

---

## 5) Alerting baseline

Recommended initial alerts:

1. `trace_dispatch_drop_detected`
   - condition: `increase(stream_kernel_trace_dispatch_submit_dropped_total[5m]) > 0`
2. `trace_sink_drop_detected`
   - condition: `sum(increase(stream_kernel_trace_sink_dropped_total[5m])) > 0`
3. `trace_shutdown_not_drained`
   - condition: last close event has `pending_total_estimate > 0`
4. `trace_queue_sustained_pressure`
   - condition: `avg_over_time(stream_kernel_trace_dispatch_queue_depth[5m])` near capacity

---

## 6) Prometheus exporter modes

Two operational modes are needed:

- `http_pull` for long-running services (FastAPI/web modes);
- `textfile` for batch/CLI runs (consumed by node_exporter textfile collector).

Unified config concept (planned):

```yaml
runtime:
  observability:
    monitoring:
      exporters:
        - kind: prometheus
          settings:
            mode: http_pull     # or textfile
            namespace: stream_kernel
            include_labels:
              process_group: true
              sink_index: true
            http:
              host: 127.0.0.1
              port: 9464
              path: /metrics
            textfile:
              path: metrics/stream_kernel.prom
```

---

## 7) Practical tuning workflow

1. Run with diagnostics enabled.
2. Check if drops are real or view-filtered.
3. If real drops:
   - enable blocking policy with timeout;
   - adjust batch size (`64 -> 128`) before queue capacity;
   - scale queue capacity after batch tuning.
4. Re-run and compare:
   - drop counters should stay flat;
   - pending at close should be zero;
   - business runtime regression should stay within agreed budget.

---

## 8) Linked implementation plan

- `docs/framework/initial_stage/_work/observability_backpressure_and_prometheus_exporter_tdd_plan.md`
