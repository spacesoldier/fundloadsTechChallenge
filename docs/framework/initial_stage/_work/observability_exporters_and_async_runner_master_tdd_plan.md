# Observability exporters + AsyncRunner expansion (master TDD plan)

## Goal

Add production-grade tracing exporter backends and asynchronous execution rails without breaking current deterministic sync baseline.

Target outcome:

- keep existing `SyncRunner` path stable;
- introduce `AsyncRunner` for async-node workloads and async observability I/O;
- support multiple OTLP/OpenTracing exporter transports behind platform adapters;
- keep exporter failures isolated from business execution path;
- preserve cross-process trace continuity (`trace_id`, `span_id`, `parent_span_id`).

## Status snapshot

- [x] Phase A baseline complete (`OBS-CFG-A-01..06`): exporter config contract,
  backend selector validation, and runner-profile compatibility checks.
- [x] Phase B complete (`OBS-REQ-01..05` + backend forwarding): requests Session backend,
  deterministic batch flush (count/timer), and exporter-level backend wiring.
- [x] Phase C complete (`OBS-HTTPX-01..05` + config normalization):
  httpx sync/async paths, HTTP/2 propagation, retry/backoff, and clean shutdown.
- [x] Phase D complete (`OBS-AIO-01..05` + queue/aiohttp config):
  aiohttp transport, deterministic drop policies, flush-on-close semantics.
- [x] Phase E complete (`OBS-U3-01..05` + urllib3 config normalization):
  pooled urllib3 transport, deterministic error isolation, headers/timeout parity.
- [x] Phase F complete (`OBS-GRPC-01..05` + grpc config normalization):
  grpc channel/stub export path, deadline/retry mapping, failure isolation.
- [x] Phase G complete (`OBS-OTELSDK-01..05`):
  OTel SDK backend path (`backend=otel_sdk`), provider/processor lifecycle, context handoff mapping.
- [x] Phase H complete (`RUN-ASYNC-01..07`):
  AsyncRunner path + runner-profile-based selection + deterministic mixed-profile contract checks.
- [x] Phase I complete (`OBS-MAT-01..04` + perf snapshot):
  compatibility matrix, parity regression, trace continuity, failure isolation, and sign-off report.
- [x] Phase J complete (`RUN-AUTO-01..06`):
  DI-driven runner auto-selection, optional explicit override contract, and validator alignment.
- [x] Phase K complete:
  full observability platform rails closure (DI-only sink resolution, system nodes,
  dependency groups, docs migration closure),
  tracked in
  [observability_platform_rails_no_hardcode_tdd_plan](observability_platform_rails_no_hardcode_tdd_plan.md).

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
  - default path: runner rail is inferred from DI dependency contracts (`sync` vs `async` bindings);
  - override path: `process_groups[].runner_profile` is optional and used only when explicitly provided;
  - invalid explicit override combinations fail at preflight/validation, not at runtime.

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

### Phase J — DI-driven runner auto-selection

Detailed subplan:

- [observability_exporters_phasej_runner_autoselect_tdd_plan](observability_exporters_phasej_runner_autoselect_tdd_plan.md)
- [observability_exporters_phasej1_async_capability_propagation_tdd_plan](observability_exporters_phasej1_async_capability_propagation_tdd_plan.md)

### Phase K — observability platform rails without hardcode

Detailed subplan:

- [observability_platform_rails_no_hardcode_tdd_plan](observability_platform_rails_no_hardcode_tdd_plan.md)

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
10. Phase J

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
