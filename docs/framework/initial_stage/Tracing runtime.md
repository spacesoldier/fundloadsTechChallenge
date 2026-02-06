# Tracing runtime (newgen)

This document defines how **runtime tracing** is configured and wired in the newgen
framework runtime. It describes config structure, CLI overrides, and the supported
trace sinks.

---

## 1. Goals

- Enable deterministic, per-step tracing for debugging and analysis.
- Keep tracing configuration **purely declarative** in config.
- Allow CLI overrides for quick enable/disable and trace path changes.

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
        - line_no
    sink:
      kind: jsonl
      jsonl:
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
- `sink.kind` (str): `jsonl` or `stdout`.
- `sink.jsonl.path` (str): path to JSONL trace file.
- `sink.jsonl.write_mode` (str): `line` (default) or `batch`.
- `sink.jsonl.flush_every_n` (int): flush cadence in lines (default 1).
- `sink.jsonl.fsync_every_n` (int|null): fsync cadence in lines (optional).

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
- `--trace-path` writes into `runtime.tracing.sink.jsonl.path`.

---

## 4. Runtime wiring

At runtime, the framework:

1) Reads `runtime.tracing`.
2) Builds `TraceRecorder` from `signature` + `context_diff`.
3) Builds a trace sink (JSONL or stdout).
4) Injects both into the execution runtime (`SyncRunner` path via runtime node wrappers).

Implementation:
- Runtime wiring: `src/stream_kernel/app/runtime.py`
- Execution engine: `src/stream_kernel/execution/runner.py`
- Trace sinks: `src/fund_load/adapters/trace_sinks.py`

---

## 5. Tests

Tracing behavior is covered by framework tests:

- `tests/stream_kernel/app/test_tracing_runtime.py`

These tests ensure:
- JSONL sink writes per-step trace lines.
- Disabled tracing does not create a sink file.

---

## 6. Notes & future extensions

Potential industrial sources for trace configuration:
- environment variables
- Kubernetes ConfigMaps / Helm values
- config registry services (Consul/etcd)
- DB-backed configs (Postgres/Redis)

The runtime should remain config-driven so these sources can be added without
changing business logic.
