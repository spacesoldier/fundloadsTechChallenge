# Observability exporters + AsyncRunner expansion (master TDD plan)

## Goal

Add production-grade tracing exporter backends and asynchronous execution rails without breaking current deterministic sync baseline.

Target outcome:

- keep existing `SyncRunner` path stable;
- introduce `AsyncRunner` for async-node workloads and async observability I/O;
- support multiple OTLP/OpenTracing exporter transports behind platform adapters;
- keep exporter failures isolated from business execution path;
- preserve cross-process trace continuity (`trace_id`, `span_id`, `parent_span_id`).

---

## Scope

In scope:

- transport backends for tracing export:
  - `requests + Session`
  - `httpx (sync/async)`
  - `aiohttp`
  - `urllib3`
  - `OTLP gRPC (grpcio)`
  - `OpenTelemetry SDK exporter (BatchSpanProcessor)`
- exporter abstraction unification and config model;
- `AsyncRunner` contract + lifecycle + DI wiring;
- batching/backpressure/drop policies;
- regression and performance characterization vs current `urllib` per-span path.

Out of scope (next waves):

- logs/metrics OTLP parity for all backends;
- distributed sampling control plane;
- adaptive auto-tuning based on runtime latency.
- full API policy-service stack (retry/circuit-breaker/auth/rate-limiter families):
  tracked separately in
  [platform_api_services_and_rate_limiter_policies_tdd_plan](platform_api_services_and_rate_limiter_policies_tdd_plan.md).

---

## Architecture constraints

- framework rails only: exporter creation through adapter discovery/DI;
- no direct exporter logic inside runner hot path;
- observability pipeline must remain non-blocking for business flow;
- deterministic fallback path must exist (`stdout/jsonl/noop`);
- config validation rejects incompatible backend/runtime combinations.
- runner selection is platform-owned and deterministic:
  - `process_groups[].runner_profile` defines primary runner rail (`sync` / `async`);
  - DI dependency contracts (`consume` vs `aconsume`, sync/async service contracts) are validated against the selected runner;
  - invalid combinations fail at preflight/validation, not at runtime.

---

## Implementation phases

Related subplan (lifecycle logging pipeline):

- [lifecycle_logging_async_pipeline_tdd_plan](lifecycle_logging_async_pipeline_tdd_plan.md)

### Phase A — common exporter contract and runtime config freeze

Detailed subplan:

- [observability_exporters_phasea_contract_and_config_tdd_plan](observability_exporters_phasea_contract_and_config_tdd_plan.md)

### Phase B — requests Session backend (sync baseline)

Detailed subplan:

- [observability_exporters_phaseb_requests_session_tdd_plan](observability_exporters_phaseb_requests_session_tdd_plan.md)

### Phase C — httpx backend (sync + async)

Detailed subplan:

- [observability_exporters_phasec_httpx_tdd_plan](observability_exporters_phasec_httpx_tdd_plan.md)

### Phase D — aiohttp backend (async high-throughput)

Detailed subplan:

- [observability_exporters_phased_aiohttp_tdd_plan](observability_exporters_phased_aiohttp_tdd_plan.md)

### Phase E — urllib3 backend (pooled low-level sync)

Detailed subplan:

- [observability_exporters_phasee_urllib3_tdd_plan](observability_exporters_phasee_urllib3_tdd_plan.md)

### Phase F — OTLP gRPC backend (grpcio)

Detailed subplan:

- [observability_exporters_phasef_otlp_grpc_tdd_plan](observability_exporters_phasef_otlp_grpc_tdd_plan.md)

### Phase G — OpenTelemetry SDK exporter backend

Detailed subplan:

- [observability_exporters_phaseg_otel_sdk_tdd_plan](observability_exporters_phaseg_otel_sdk_tdd_plan.md)

### Phase H — AsyncRunner rollout

Detailed subplan:

- [execution_asyncrunner_phaseh_tdd_plan](execution_asyncrunner_phaseh_tdd_plan.md)

### Phase I — matrix regression + perf/parity sign-off

Detailed subplan:

- [observability_exporters_phasei_regression_perf_tdd_plan](observability_exporters_phasei_regression_perf_tdd_plan.md)

---

## Delivery order

1. Phase A
2. Phase B
3. Phase C
4. Phase H (minimal async path)
5. Phase D
6. Phase E
7. Phase F
8. Phase G
9. Phase I

Reason:

- stabilize contracts first;
- land low-risk sync backend first;
- then async rails;
- then specialized/high-complexity transports;
- close with full matrix regression/perf gate.

---

## Global acceptance criteria

- same business output parity on existing baseline/experiment configs;
- Jaeger still shows continuous traces across process groups;
- exporter failure does not break execution;
- configurable bounded memory footprint for observability queues/buffers;
- no per-span blocking network call in default production profile.
