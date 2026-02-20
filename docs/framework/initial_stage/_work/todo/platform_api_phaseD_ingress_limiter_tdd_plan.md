# Platform API Phase D: ingress limiter integration (TDD)

## Status

- [x] Step A — docs + RED tests (`API-ING-01..03`)
- [x] Step B — GREEN implementation (DI binding + ingress enforcement)
- [x] Step C — integration regression on orchestration/config subsets

## Goal

Integrate rate-limiter policy enforcement into ingress path before payload enters
business graph execution.

Baseline for this phase:

- enforcement point: graph-native source ingress wrappers (`source:*`);
- policy source: `runtime.web.interfaces[].policies.rate_limit`;
- limiter instance: platform `RateLimiterService` via DI;
- blocked ingress request produces deterministic terminal error outcome.

---

## Contracts

### 1) Policy resolution

- if at least one web interface declares `policies.rate_limit`, framework binds
  a dedicated qualifier:
  - `RateLimiterService#web.ingress.default`
- if no web interface declares limiter policy, ingress limiter is disabled.

### 2) Enforcement point

- enforcement happens inside source ingress wrapper before:
  - context seeding,
  - downstream routing to business nodes.

### 3) Reject behavior

When limiter denies ingress message:

- produce `TerminalEvent(status=\"error\")` with deterministic payload:
  - `{\"code\": \"rate_limited\", \"status_code\": 429}`
- preserve `trace_id` and `reply_to` if available.

### 4) Observability hook

Ingress wrapper emits decision event via optional observability hook:

- `on_ingress_rate_limit_decision(...)` (best-effort optional callback)
- includes: `trace_id`, `allowed`, `source_node`, `source_role`, limiter profile.

---

## TDD steps

### Step A — docs + RED tests

- `API-ING-01` deterministic reject path with status 429.
- `API-ING-02` websocket-like per-key throttling (same `reply_to` key).
- `API-ING-03` decision hook emitted for allow/deny outcomes.

### Step B — GREEN implementation

- bind web-ingress limiter qualifier in runtime builder;
- wire ingress limiter into source wrapper creation;
- enforce allow/deny before context seed + routing.

### Step C — integration and regression

- validate builder/child bootstrap integration still green;
- ensure no behavior change when `runtime.web.interfaces[].policies.rate_limit` absent.

---

## Test catalog

- `tests/stream_kernel/execution/orchestration/test_builder.py`
  - `test_api_ing_01_source_ingress_rejects_when_web_limiter_exceeded`
  - `test_api_ing_02_source_ingress_uses_reply_to_as_rate_limit_key`
  - `test_api_ing_03_source_ingress_emits_limiter_decision_observability_hook`

---

## Notes

- This phase does not yet add FastAPI runtime endpoints; it hardens framework ingress rails.
- Multi-interface/profile routing will be extended in next phases with explicit interface-to-adapter mapping.
- Validation commands used in this phase:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k 'api_ing_0'`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py tests/stream_kernel/execution/orchestration/test_child_bootstrap.py`
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_rate_limiter_service.py tests/stream_kernel/config/test_newgen_validator.py`
