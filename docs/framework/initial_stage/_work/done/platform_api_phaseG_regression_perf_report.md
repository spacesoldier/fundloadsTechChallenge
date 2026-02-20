# Phase G Regression/Perf Report

## Command

```bash
.venv/bin/python - <<'PY'
from __future__ import annotations
import statistics, time
from stream_kernel.platform.services.api.outbound import InMemoryOutboundApiService
from stream_kernel.platform.services.api.policy import InMemoryRateLimiterService

N=5000

def run(service, label):
    lat=[]
    start=time.perf_counter()
    for i in range(N):
        t0=time.perf_counter()
        service.call(operation=lambda i=i: i, key='bench', trace_id=f't{i}')
        lat.append((time.perf_counter()-t0)*1e6)
    total=time.perf_counter()-start
    print(label, f"throughput={N/total:.2f}/s", f"avg_us={statistics.mean(lat):.2f}", f"p95_us={statistics.quantiles(lat, n=100)[94]:.2f}")

svc_off = InMemoryOutboundApiService(policy_config={}, limiter=None)
svc_on = InMemoryOutboundApiService(
    policy_config={"rate_limit": {"kind":"fixed_window", "limit": 1000000, "window_ms": 60000}},
    limiter=InMemoryRateLimiterService(limiter_config={"kind":"fixed_window", "limit": 1000000, "window_ms": 60000}),
)
run(svc_off, 'policy_off')
run(svc_on, 'policy_on_fixed_window')
PY
```

## Environment

- Runtime: local `.venv` Python 3.13
- Workload: 5000 sequential outbound calls
- Mode A: policy disabled (`policy_config={}`)
- Mode B: policy enabled (`fixed_window`, high quota to avoid blocking)

## Results

- `policy_off`
  - throughput: `237346.00 req/s`
  - avg latency: `4.14 us`
  - p95 latency: `4.23 us`
- `policy_on_fixed_window`
  - throughput: `61419.84 req/s`
  - avg latency: `16.20 us`
  - p95 latency: `17.65 us`

## Notes

- Numbers are characterization metrics, not hard SLA thresholds.
- Main overhead comes from limiter state lookup/update and policy diagnostics path.
- Phase G parity gates are additionally covered by `API-REG-01..03` tests.
