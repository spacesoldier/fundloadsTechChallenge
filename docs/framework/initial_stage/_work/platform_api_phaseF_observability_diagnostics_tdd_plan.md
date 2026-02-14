# Platform API Phase F: observability and diagnostics (TDD)

## Status

- [x] Step A — docs + RED tests (`API-OBS-01..03`)
- [x] Step B — GREEN implementation (counters + markers + redaction)
- [x] Step C — integration/regression on policy/config/orchestration subsets

## Goal

Harden outbound policy-chain observability so policy decisions are inspectable and safe:

- expose deterministic limiter/pipeline counters;
- emit outbound policy decision markers for tracing/telemetry rails;
- enforce diagnostics redaction for auth/header/token-like values.

---

## Contracts

### 1) Counter surface

Outbound policy service exports deterministic counters:

- `allowed`
- `blocked`
- `queued`
- `dropped`

Counters are monotonic within service lifetime and do not expose payload/secret values.

### 2) Policy decision markers

Outbound service emits best-effort marker hook:

- `on_outbound_policy_decision(...)`

Expected fields:

- `trace_id`
- `key`
- `profile`
- `stage` (`limiter|retry|circuit_breaker|call`)
- `decision` (stable decision token)

### 3) Diagnostics redaction

Diagnostic events include sanitized policy snapshot:

- redact secret-bearing keys (`secret`, `token`, `authorization`, `api_key`, `headers`, etc.);
- never leak raw auth header/token values.

---

## TDD steps

### Step A — docs + RED tests

- `API-OBS-01` counters exported and updated on allow/deny paths.
- `API-OBS-02` policy decision markers emitted with trace correlation.
- `API-OBS-03` diagnostics snapshots redact sensitive values.

### Step B — GREEN implementation

- extend outbound service contract with diagnostics methods;
- add decision marker emission;
- add recursive redaction utility for diagnostics snapshot/events.

### Step C — integration/regression

- keep `API-EGR-*` and policy wiring tests green;
- ensure builder DI bindings for outbound service remain unchanged.

---

## Test catalog

- `tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
  - `test_api_obs_01_outbound_service_exports_limiter_counters`
  - `test_api_obs_02_outbound_service_emits_policy_decision_markers`
  - `test_api_obs_03_outbound_service_diagnostics_redact_sensitive_auth_headers`

## Validation commands

- `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_outbound_api_service.py -k 'api_obs_'`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_outbound_api_service.py -k 'api_egr_'`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_rate_limiter_service.py tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k 'runtime_api_policy_bindings'`
- `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'api_policies'`
