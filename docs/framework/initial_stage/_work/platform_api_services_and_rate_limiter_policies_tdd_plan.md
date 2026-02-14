# Platform API services and rate-limiter policies (TDD plan)

## Goal

Introduce framework-native platform services for outbound/inbound API integration
with deterministic policy controls:

- timeout
- retry
- circuit-breaker
- auth
- telemetry/tracing
- batching
- rate limiting

The objective is to keep business nodes free from transport/library specifics and
bind all API behavior to platform ports/services via DI.

## Status

- [x] Phase A started: validator contract for `runtime.platform.api_policies` and
  `runtime.web.interfaces[].policies` is wired and covered by tests.
- [x] Phase B baseline started: platform policy services are DI-bindable, profile
  qualifier wiring is implemented, and runner/profile compatibility guard is added.
- [x] Phase C baseline started: in-memory limiter algorithms (`fixed_window`,
  `sliding_window_counter`, `sliding_window_log`, `token_bucket`, `leaky_bucket`,
  `concurrency`) are implemented with deterministic test clock support and covered by
  `API-LIM-01..05`.
- [x] Phase D baseline completed: ingress limiter enforcement is integrated on
  graph-native source ingress wrappers with deterministic 429 reject and
  observability decision hook (`API-ING-01..03`).
- [x] Phase E baseline completed: outbound policy-chain service is integrated
  (rate-limit + retry + circuit-breaker gate) with DI profile bindings and
  `API-EGR-01..03` coverage.
- [x] Phase F baseline completed: outbound policy observability/diagnostics are
  integrated (`API-OBS-01..03`) with counters, decision markers and redaction.
- [x] Phase G baseline completed: parity/perf gate is green (`API-REG-01..03`)
  and characterization report is committed.

---

## Scope

In scope:

- platform service contract for API calls (request/response + stream variants);
- adapter-backed implementations (httpx/aiohttp/requests/grpcio/urllib3);
- policy composition chain at platform service level;
- ingress and egress rate-limiter policies;
- deterministic error mapping and observability counters.

Out of scope:

- global distributed quota service across multiple clusters;
- dynamic policy reconfiguration control plane;
- per-tenant billing integration.

---

## Policy model (rate limiters)

Supported limiter families (initial target set):

1. Fixed window
2. Sliding window counter
3. Sliding window log
4. Token bucket
5. Leaky bucket
6. Concurrency limiter (max in-flight)

Limiter placement:

- inbound (web ingress adapters)
- outbound (platform API service adapters)
- optional per-route/per-endpoint/per-remote-service qualifiers.

---

## Runtime contract extension (draft)

`runtime.platform.api_policies`:

- `defaults.timeout_ms`
- `defaults.retry`
- `defaults.circuit_breaker`
- `defaults.auth`
- `defaults.rate_limit`

`runtime.web.interfaces[].policies`:

- `rate_limit`
- `request_size_bytes`
- `timeout_ms`

`runtime.platform.process_groups[].services` (optional bindings):

- `api_service_profile`
- `rate_limiter_profile`

---

## TDD phases

### Phase A — contract freeze and validator

RED tests:

- `API-POL-CFG-01` unknown limiter kind rejected.
- `API-POL-CFG-02` invalid limiter parameters rejected deterministically.
- `API-POL-CFG-03` conflicting policy declarations (route + global) resolved by explicit precedence rules.
- `API-POL-CFG-04` backward compatibility with no policy block.

GREEN target:

- validator normalizes policy model and defaults.

### Phase B — policy service contracts and DI wiring

RED tests:

- `API-POL-DI-01` platform API service resolved via DI in node/service contexts.
- `API-POL-DI-02` limiter service resolved by qualifier/profile.
- `API-POL-DI-03` invalid runner/profile + async/sync policy dependency mismatch rejected preflight.

GREEN target:

- policy services are framework-managed and discovery-driven.

### Phase C — limiter algorithm implementations

RED tests:

- `API-LIM-01` fixed window allows/blocks deterministically.
- `API-LIM-02` sliding window counter accuracy within configured tolerance.
- `API-LIM-03` token bucket refill/consume semantics deterministic under simulated clock.
- `API-LIM-04` leaky bucket drain behavior deterministic under burst.
- `API-LIM-05` concurrency limiter enforces in-flight cap with clean release on success/error.

GREEN target:

- in-memory limiter adapters for all algorithms.

### Phase D — ingress integration (FastAPI adapters)

Detailed subplan:

- [platform_api_phaseD_ingress_limiter_tdd_plan](platform_api_phaseD_ingress_limiter_tdd_plan.md)

RED tests:

- `API-ING-01` request rejected with deterministic status when limiter is exceeded.
- `API-ING-02` websocket stream frames obey per-connection or per-key limit.
- `API-ING-03` limiter decision emits observability events.

GREEN target:

- web ingress path enforces configured limiter policy before routing to business graph.

### Phase E — outbound integration (platform API services)

Detailed subplan:

- [platform_api_phaseE_outbound_policy_chain_tdd_plan](platform_api_phaseE_outbound_policy_chain_tdd_plan.md)

RED tests:

- `API-EGR-01` outbound call blocked/deferred by limiter according to policy.
- `API-EGR-02` retry + limiter interaction preserves deterministic max-attempt contract.
- `API-EGR-03` circuit-breaker + limiter composition order is deterministic and documented.

GREEN target:

- outbound platform API service composes timeout/retry/circuit-breaker/auth/limiter policies.

### Phase F — observability and diagnostics

Detailed subplan:

- [platform_api_phaseF_observability_diagnostics_tdd_plan](platform_api_phaseF_observability_diagnostics_tdd_plan.md)

RED tests:

- `API-OBS-01` limiter counters exported (`allowed`, `blocked`, `queued`, `dropped`).
- `API-OBS-02` traces include policy decision markers.
- `API-OBS-03` diagnostics redact sensitive auth/headers.

GREEN target:

- policy actions are visible in tracing/telemetry/monitoring rails.

### Phase G — regression/perf gate

Detailed subplan:

- [platform_api_phaseG_regression_perf_gate_tdd_plan](platform_api_phaseG_regression_perf_gate_tdd_plan.md)

RED tests:

- `API-REG-01` baseline business outputs unchanged when policies disabled.
- `API-REG-02` policy-enabled mode remains deterministic for same input/time source.
- `API-REG-03` throughput/latency characterization report committed.

GREEN target:

- parity gate + performance envelope documented.

---

## Exit criteria

- policy model documented and validated;
- limiter algorithms available as platform services/adapters;
- ingress and outbound policy hooks integrated;
- observability coverage and deterministic behavior verified.
