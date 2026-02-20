# Phase F Supervisor Transport-Only Regression/Perf Report

## Command

```bash
.venv/bin/python - <<'PY'
from __future__ import annotations
import json, re, subprocess, time

CASES = [
    ("baseline_no_obs", "src/fund_load/baseline_config_newgen_multiprocess.yml"),
    ("experiment_logical_otlp", "src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml"),
]
pattern_kv = re.compile(r"\b([a-zA-Z0-9_]+)=([^\s]+)")

def parse_line(line: str):
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            obj = json.loads(line)
        except Exception:
            return None
        fields = obj.get("fields", {}) if isinstance(obj, dict) else {}
        if not isinstance(fields, dict):
            fields = {}
        return {"kind": fields.get("kind"), "ts": fields.get("ts_epoch_ms"), "fields": fields}
    if "bootstrap." not in line:
        return None
    kind = None
    m = re.search(r"bootstrap\.([a-zA-Z0-9_]+)", line)
    if m:
        kind = m.group(1)
    fields = {}
    for k, v in pattern_kv.findall(line):
        fields[k] = v.strip(",")
    ts = None
    if "ts_epoch_ms" in fields:
        try:
            ts = int(fields["ts_epoch_ms"])
        except Exception:
            ts = None
    return {"kind": kind, "ts": ts, "fields": fields}

for label, cfg in CASES:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [".venv/bin/python", "-m", "fund_load", "--config", cfg],
        capture_output=True,
        text=True,
        timeout=120,
    )
    wall_s = time.perf_counter() - t0
    entries = [e for e in (parse_line(line) for line in proc.stdout.splitlines()) if e is not None]
    start = max_ready = max_boot = max_stop = None
    close_begin = None
    for e in entries:
        kind, ts = e["kind"], e["ts"]
        if kind == "supervisor_start_groups" and isinstance(ts, int):
            start = ts if start is None else min(start, ts)
        if kind == "worker_ready" and isinstance(ts, int):
            max_ready = ts if max_ready is None else max(max_ready, ts)
        if kind == "worker_bootstrapped" and isinstance(ts, int):
            max_boot = ts if max_boot is None else max(max_boot, ts)
        if kind == "worker_stopped" and isinstance(ts, int):
            max_stop = ts if max_stop is None else max(max_stop, ts)
        if kind == "trace_dispatch_diagnostics" and e["fields"].get("stage") == "close_begin":
            close_begin = e["fields"]
    startup_ready_ms = (max_ready - start) if isinstance(start, int) and isinstance(max_ready, int) else None
    startup_boot_ms = (max_boot - start) if isinstance(start, int) and isinstance(max_boot, int) else None
    runtime_ms = (max_stop - start) if isinstance(start, int) and isinstance(max_stop, int) else None
    print(label, proc.returncode, f"wall_s={wall_s:.3f}", startup_ready_ms, startup_boot_ms, runtime_ms)
    print("close_begin", close_begin)
PY
```

## Environment

- Date: February 20, 2026
- Runtime: local `.venv` (Python 3.13.2)
- Host: Linux workstation
- Dataset: `input.txt` from repository root
- Mode A: no observability exporters (`baseline_config_newgen_multiprocess.yml`)
- Mode B: dedicated observability process + `otel_otlp_logical` exporter
  (`experiment_config_newgen_multiprocess_jaeger.yml`)

## Results

| Case | Wall time (s) | Startup ready (ms) | Startup bootstrapped (ms) | Run window (ms) |
| --- | ---: | ---: | ---: | ---: |
| `baseline_no_obs` | `1.326` | `318` | `317` | `1033` |
| `experiment_logical_otlp` | `6.354` | `442` | `442` | `5927` |

Queue/dispatch diagnostics from `trace_dispatch_diagnostics stage=close_begin`:

| Case | dispatch_wait_count | dispatch_wait_ms_total | dispatch_wait_ms_max | dispatch_submitted | dispatch_processed | dispatch_dropped | control_dropped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_no_obs` | `8` | `618` | `205` | `0` | `0` | `0` | `0` |
| `experiment_logical_otlp` | `16485` | `6082` | `545` | `16179` | `16179` | `15821` | `15974` |

Interpretation:

- Startup latency regression is moderate (~124 ms by ready marker), but total run window grows by ~4.9 s under logical OTLP load.
- The bottleneck signature is control-plane pressure (`dispatch_wait_count`, `dispatch_dropped`, `control_dropped`), not business dispatch failures.

## Migration Notes

- Dedicated observability owner mode remains mandatory for multiprocess experiments with OTLP exporters.
- Supervisor keeps only transport/lifecycle responsibilities and no longer uses inline trace-forward fallback when dedicated trace dispatch loop is unavailable.
- Existing defaults remain intentionally conservative in this phase:
  - dispatch queue policy defaults to `drop_newest`,
  - queue sizes keep current baseline to avoid widening memory envelope without additional perf sweeps.
- Operators should treat `dispatch_dropped` and `control_dropped` as first-tier health signals for trace completeness.

## Operator Runbook

1. Run the benchmark command above for baseline and experiment configs before changing queue policy/size.
2. Watch lifecycle diagnostics (`bootstrap.trace_dispatch_diagnostics`) at `close_begin` and `force_terminate_requested`.
3. If `control_dropped` is non-zero:
   - reduce exporter pressure (fewer slices/exporters or smaller trace cardinality),
   - or scale control-plane capacity in config (`dispatch_queue.max_items`, service worker queue).
4. If `dispatch_wait_ms_max` grows with low throughput, inspect transport path first (IPC control channel saturation), then exporter sink backpressure.
5. Keep alerting thresholds aligned with diagnostics counters exposed to monitoring (`dispatch_*`, `control_*`, `business_*`).
