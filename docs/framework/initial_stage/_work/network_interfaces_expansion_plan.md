# Network interfaces expansion plan

## Goal

Add network-facing ingress/egress support (HTTP, WebSocket, HTTP/2 stream, GraphQL)
without leaking protocol details into business nodes.

The target model remains framework-native:

- business nodes work with domain models;
- protocol adapters live in platform layer;
- routing/execution use the same `Envelope` + `Router` + `Runner` mechanics;
- no special-case runtime branches for specific protocols.

Codec/backend alignment:

- network adapters and file adapters must reuse the same transport codec layer;
- see [Transport codecs and file IO options](../Transport%20codecs%20and%20file%20IO%20options.md).
- process-isolated execution and secure IPC rollout details:
  [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md).
- multiprocess supervisor + observability substrate prerequisite (Phase 5pre):
  [web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan](web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md).
- exporter backends + AsyncRunner rollout roadmap (post-5pre):
  [observability_exporters_and_async_runner_master_tdd_plan](observability_exporters_and_async_runner_master_tdd_plan.md).
- non-blocking lifecycle logging pipeline (supervisor/workers):
  [lifecycle_logging_async_pipeline_tdd_plan](lifecycle_logging_async_pipeline_tdd_plan.md).
- platform API service policies (timeout/retry/circuit-breaker/auth/telemetry/rate-limit):
  [platform_api_services_and_rate_limiter_policies_tdd_plan](platform_api_services_and_rate_limiter_policies_tdd_plan.md).
- multiprocess bus ownership and outbound routing cache target model:
  [../web/analysis/Multiprocess bus ownership and outbound route cache](../web/analysis/Multiprocess%20bus%20ownership%20and%20outbound%20route%20cache.md).
- bootstrap-process and generated-secret rollout details (Phase 3):
  [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md).
- Phase 3 Step A contract freeze spec:
  [web_phase3_stepa_contract_freeze_spec](web_phase3_stepa_contract_freeze_spec.md).
- Phase 4 reply-correlation rollout details:
  [web_phase4_reply_correlation_tdd_plan](web_phase4_reply_correlation_tdd_plan.md).

## Scope

In scope:

- platform adapters for network protocols;
- mapping protocol payloads to domain models at adapter boundary;
- request/response and stream semantics in one model;
- observability at ingress/egress boundaries.
- ingress/outbound policy enforcement via platform services:
  timeout, retry, circuit-breaker, auth, telemetry, rate-limiting.

Out of scope (later stages):

- authN/authZ policy engine;
- distributed backpressure across cluster;
- production-grade retry/DLQ policies for all transports.

## Phases (TDD-first)

Phase ordering note:

- before network interface implementation phases, complete web `Phase 5pre`
  (real multiprocess supervisor + OTel/OpenTracing substrate).

1. Contract freeze (ports and semantics)
- [ ] Freeze contract mapping:
  - HTTP request/response -> `request` / `response`
  - WebSocket and server streaming -> `stream`
  - keyed protocol streams -> `kv_stream`
- [ ] Document envelope rules for ingress metadata (`trace_id`, `source`, `transport`).
- [ ] Define strict validation for unsupported transport kinds.

2. Config model and validation
- [ ] Add runtime config section for network interface declarations.
- [ ] Validate interface list shape and supported `kind` values.
- [ ] Validate each interface binds only to stable framework port types.

3. Adapter discovery and wiring
- [ ] Add platform adapter discovery modules for network adapters.
- [ ] Ensure adapters are instantiated from discovery/registry only (no runtime hardcode).
- [ ] Verify adapter names in config resolve deterministically.

4. Execution integration
- [ ] Route ingress payloads into runtime via standard queue port.
- [ ] Route egress domain models into response/stream adapters.
- [ ] Keep runner unaware of protocol internals.

5. Observability integration
- [ ] Emit tracing spans at network boundaries (ingress receive / egress send).
- [ ] Emit telemetry counters (inbound/outbound rates, queue lag).
- [ ] Emit monitoring signals for dropped/invalid messages.

6. Baseline tests for first network adapter set
- [ ] HTTP request adapter (ingress) integration test.
- [ ] HTTP response adapter (egress) integration test.
- [ ] WebSocket stream adapter integration test.
- [ ] GraphQL adapter integration test (query/mutation baseline).

7. Policy services and rate limiters
- [ ] Implement framework policy-service chain for inbound/outbound API calls.
- [ ] Support limiter families: fixed window, sliding window (counter/log), token bucket, leaky bucket, concurrency cap.
- [ ] Add deterministic observability for policy decisions (`allowed/blocked/queued/dropped`).

## TDD test matrix (starter)

`NET-VAL-01` config validator rejects unknown network interface kind.

`NET-VAL-02` config validator rejects interface bind outside stable port set.

`NET-WIRE-01` discovery resolves configured network adapter by name.

`NET-RUN-01` ingress adapter emits domain model, router delivers to consumers.

`NET-RUN-02` node emits response model, response adapter receives exactly once.

`NET-OBS-01` tracing observer records ingress and egress boundary events.

`NET-ERR-01` malformed inbound payload is rejected with monitoring event and no node call.
