# Phase F Perf/Isolation Report

## Command

```bash
.venv/bin/python - <<'PY'
from time import perf_counter
from stream_kernel.platform.services.observability import NoOpObservabilityService, FanoutObservabilityService

class Rec:
    def before_node(self, **kw): return None
    def after_node(self, **kw): return None
    def on_node_error(self, **kw): return None
    def on_run_end(self): return None
    def on_trace_event(self, **kw): return None
    def on_log_event(self, **kw): return None
    def on_metric_event(self, **kw): return None
    def on_monitoring_event(self, **kw): return None

class FailTrace(Rec):
    def on_trace_event(self, **kw): raise RuntimeError("boom")

def bench(label, fn, n=200000):
    t0 = perf_counter()
    for _ in range(n): fn()
    dt = perf_counter() - t0
    print(f"{label}|{n}|{dt:.6f}|{(dt/n)*1e6:.3f}")

noop = NoOpObservabilityService()
one = FanoutObservabilityService(observers=[Rec()])
full = FanoutObservabilityService(observers=[Rec(), Rec(), Rec(), Rec()])
isolated = FanoutObservabilityService(observers=[FailTrace(), Rec()])

bench("noop_publish_trace", lambda: noop.publish_trace(event={"x": 1}, trace_id="t1", attributes={"k": "v"}))
bench("fanout1_publish_trace", lambda: one.publish_trace(event={"x": 1}, trace_id="t1", attributes={"k": "v"}))
bench("fanout4_publish_trace", lambda: full.publish_trace(event={"x": 1}, trace_id="t1", attributes={"k": "v"}))
bench("fanout_isolated_failure_publish_trace", lambda: isolated.publish_trace(event={"x": 1}, trace_id="t1", attributes={"k": "v"}))
PY
```

## Environment

- Python: 3.12 (`.venv`)
- Host: local dev machine
- Iterations per case: `200000`
- Measured metric: wall-clock total seconds and mean microseconds per call

## Results

| Case | Iterations | Total seconds | Mean us/call |
| --- | ---: | ---: | ---: |
| `noop_publish_trace` | 200000 | 0.036469 | 0.182 |
| `fanout1_publish_trace` | 200000 | 0.238312 | 1.192 |
| `fanout4_publish_trace` | 200000 | 0.501286 | 2.506 |
| `fanout_isolated_failure_publish_trace` | 200000 | 0.402771 | 2.014 |

## Notes

- Optional callback isolation is active: failing observer callback no longer breaks fan-out.
- Sync baseline (`NoOpObservabilityService`) remains lightweight (`~0.18 us/call` in this run).
- Full fan-out overhead scales with observer count as expected and remains bounded.
