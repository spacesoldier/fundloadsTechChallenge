# Web Phase 5pre Step A: contract freeze spec (multiprocess supervisor + observability)

## Goal

Freeze configuration contracts for:

- multiprocess supervisor lifecycle knobs;
- readiness-gated workload start;
- platform logging pipeline (no direct `print` in runtime paths);
- observability exporter declarations for tracing/logging.

This spec defines validator-level shape and defaults only.
Behavioral implementation is covered in later 5pre steps.

---

## Compatibility policy (Step A)

To avoid breaking existing runtime/profile tests in one jump:

- existing `runtime.platform.execution_ipc` root fields stay valid;
- new `runtime.platform.execution_ipc.control` is introduced as optional in Step A;
- strict requirement to use control-only mode is deferred to later execution steps.

In other words: Step A freezes the target schema while preserving backward compatibility.

---

## Contract additions

## 1) `runtime.observability`

### 1.1 `runtime.observability.tracing.exporters[]`

Exporter shape:

- `kind: str` in:
  - `jsonl`
  - `stdout`
  - `otel_otlp`
  - `opentracing_bridge`
- `settings: mapping` (optional, defaults to `{}`)

### 1.2 `runtime.observability.logging`

- `exporters: list[mapping]` (optional, defaults to `[]`)
- each exporter:
  - `kind: str` in:
    - `stdout`
    - `jsonl`
    - `otel_logs_otlp`
  - `settings: mapping` (optional, defaults to `{}`)
- `lifecycle_events: mapping` (optional, defaults to `{}`)
  - `enabled: bool` (default `true`)
  - `level: str` in `info|debug` (default `info`)

---

## 2) `runtime.platform.readiness`

Optional mapping:

- `enabled: bool` (default `true`)
- `start_work_on_all_groups_ready: bool` (default `true`)
- `readiness_timeout_seconds: int` (default `30`, must be `> 0`)

---

## 3) `runtime.platform.process_groups[]` lifecycle knobs

Per-group optional fields:

- `workers: int` (default `1`, must be `> 0`)
- `runner_profile: str` (default `sync`, non-empty)
- `heartbeat_seconds: int` (default `5`, must be `> 0`)
- `start_timeout_seconds: int` (default `30`, must be `> 0`)
- `stop_timeout_seconds: int` (default `30`, must be `> 0`)

Existing selector fields remain valid:

- `stages`, `tags`, `runners`, `nodes`.

---

## 4) `runtime.platform.execution_ipc.control` (optional in Step A)

Optional mapping with same baseline transport guard semantics:

- `transport: tcp_local` (default `tcp_local`)
- `bind_host: 127.0.0.1` for `tcp_local`
- `bind_port: int` in `[0, 65535]`
- `auth: mapping`
  - `mode: hmac`
  - `ttl_seconds: int > 0`
  - `nonce_cache_size: int > 0`
- `max_payload_bytes: int > 0`

---

## Step A test matrix

- `P5PRE-CFG-01` reject non-int `process_groups[].workers`.
- `P5PRE-CFG-02` reject non-positive `process_groups[].workers`.
- `P5PRE-CFG-03` reject invalid `runtime.platform.readiness` field types/values.
- `P5PRE-CFG-04` reject unknown `runtime.observability.tracing.exporters[].kind`.
- `P5PRE-CFG-05` reject unknown `runtime.observability.logging.exporters[].kind`.
- `P5PRE-CFG-06` reject unknown `runtime.observability.logging.lifecycle_events.level`.
- `P5PRE-CFG-07` reject invalid `runtime.platform.execution_ipc.control` shape.
- `P5PRE-CFG-08` accept valid Step A contract with defaults normalized.

---

## Exit criteria (Step A)

- validator accepts new contract shapes with deterministic defaults;
- validator rejects malformed values with deterministic error categories/messages;
- legacy execution_ipc root profile remains valid for current test baseline;
- no runtime behavior changes are introduced in Step A.
