# Platform API Phase E: outbound policy chain integration (TDD)

## Status

- [x] Step A — docs + RED tests (`API-EGR-01..03`)
- [x] Step B — GREEN implementation (outbound service + DI bindings)
- [x] Step C — integration/regression on policy/config/orchestration subsets

## Goal

Integrate deterministic outbound policy execution into platform API service rails:

- rate-limiter enforcement for outbound calls;
- retry policy with bounded attempts;
- circuit-breaker gate with deterministic open/half-open/closed transitions.

This phase is service-level integration: business nodes keep calling platform services
and do not embed transport/policy logic.

---

## Contracts

### 1) Service contract

- Framework provides `OutboundApiService` as a platform service.
- Service is profile-aware via DI qualifiers:
  - default policy chain (`qualifier=None`);
  - profile policy chain (`qualifier=<profile_name>`).

### 2) Composition order (deterministic)

Outbound attempts run in strict order:

1. limiter check (`allow`)
2. circuit-breaker gate
3. call execution
4. retry decision on failure

This order is stable and documented to avoid ambiguous behavior.

### 3) Deterministic errors

- limiter deny -> `OutboundRateLimitedError`
- circuit open/half-open deny -> `OutboundCircuitOpenError`
- final call failure after retry budget -> original exception re-raised

### 4) Retry semantics

- total attempts: `1 + retry.max_attempts`
- retry budget is deterministic and bounded
- retry delay (`retry.backoff_ms`) is injected through sleep hook for testability

---

## TDD steps

### Step A — docs + RED tests

- `API-EGR-01` limiter blocks outbound call before transport invocation.
- `API-EGR-02` retry budget is respected and bounded.
- `API-EGR-03` limiter + circuit-breaker order is stable and observable through call log.

### Step B — GREEN implementation

- add outbound platform service contract + baseline implementation;
- wire default/profile outbound services into DI runtime binding;
- persist circuit-breaker state via platform KV port.

### Step C — integration/regression

- keep existing API policy/limiter tests green;
- ensure builder/runtime wiring resolves outbound service per qualifier.

---

## Test catalog

- `tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
  - `test_api_egr_01_outbound_call_is_blocked_by_limiter`
  - `test_api_egr_02_retry_budget_is_bounded_and_deterministic`
  - `test_api_egr_03_limiter_then_circuit_gate_order_is_deterministic`
- `tests/stream_kernel/execution/orchestration/test_builder.py`
  - DI binding coverage for outbound service qualifiers.

---

## Notes

- This phase does not yet introduce concrete HTTP/grpc client libraries.
- Transport-specific adapters (requests/httpx/aiohttp/urllib3/grpc/OTel SDK path)
  will reuse this policy-chain service in next phases.
- Validation commands used in this phase:
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_rate_limiter_service.py tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k 'runtime_api_policy_bindings'`
  - `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'api_policies or process_group_services_rejects_unknown_key'`
