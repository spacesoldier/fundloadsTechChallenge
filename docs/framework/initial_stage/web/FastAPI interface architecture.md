# FastAPI interface architecture

This document defines how HTTP/WebSocket/GraphQL interfaces are integrated into
the framework without introducing runtime hardcode.

It complements:

- [Router and DAG roadmap](../Router%20and%20DAG%20roadmap.md)
- [Execution runtime and routing integration](../Execution%20runtime%20and%20routing%20integration.md)
- [Ports and adapters model](../Ports%20and%20adapters%20model.md)
- [Transport codecs and file IO options](../Transport%20codecs%20and%20file%20IO%20options.md)
- [Runner loop orchestration](Runner%20loop%20orchestration.md)
- [analysis/Async runtime models and event loop internals](analysis/Async%20runtime%20models%20and%20event%20loop%20internals.md)
- [analysis/Web-execution process isolation and Redis backbone](analysis/Web-execution%20process%20isolation%20and%20Redis%20backbone.md)
- [analysis/Execution process port security profile](analysis/Execution%20process%20port%20security%20profile.md)
- [analysis/Multiprocess bus ownership and outbound route cache](analysis/Multiprocess%20bus%20ownership%20and%20outbound%20route%20cache.md)

---

## 1) Goals

- Programmatic endpoint declaration driven by framework config/discovery.
- Request/reply correlation with deterministic response routing.
- Unified transport model for HTTP, HTTP/2 streams, WebSocket, and GraphQL.
- No protocol-specific branching in business nodes or core runner.

---

## 2) Endpoint model (programmatic)

The framework owns endpoint construction through adapter discovery.

Target implementation capabilities:

- dynamic route registration (`APIRouter`, `add_api_route`)
- dynamic websocket route registration (`add_api_websocket_route`)
- shared dependency graph per route group
- typed request/response bindings from config/contracts

Endpoint definitions are configuration + discovery artifacts, not hardcoded app
functions in project code.

---

## 3) Correlated request/reply routing

### 3.1 Ingress path

1. Network ingress adapter receives request.
2. It allocates or reads `trace_id`/`request_id`.
3. It stores `reply_to` metadata in context.
4. It emits framework transport payload into routing/execution.

### 3.2 Egress path

1. Business pipeline emits response models (success/error).
2. Router resolves response sink route by `reply_to`.
3. Egress adapter sends response to original caller.
4. Waiter/future is completed and cleaned up deterministically.

This enables one framework pattern for sync HTTP reply and async stream push.

---

## 4) Port mapping

- HTTP request/response: `request` / `response`
- WebSocket data stream: `stream`
- keyed protocol stream: `kv_stream`

Adapters bind to stable port contracts only; transport details stay in adapter settings.

---

## 5) Protocol support notes

### 5.1 HTTP + HTTP/2

- FastAPI supports HTTP endpoints.
- HTTP/2 behavior depends on ASGI server backend (for example Hypercorn).
- Streaming responses are adapter-managed and route through framework ports.

### 5.2 WebSocket

- FastAPI supports websocket routes.
- WS ingress/egress adapters map frames to framework stream models.

### 5.3 GraphQL

- GraphQL is integrated as adapter/router layer (for example Strawberry router).
- Resolvers emit/consume framework transport/domain models via the same routing core.

---

## 6) Observability at web boundaries

Web adapters must emit boundary observability events:

- ingress accepted/rejected
- routing handoff
- egress delivered/failed

These events flow through existing observability service contracts.

---

## 7) Security and policy hooks (initial stage)

Initial stage tracks hook points only:

- auth/authz dependencies at adapter boundary
- request-size/rate limits as ingress policies
- deterministic timeout/cancel behavior for awaiting replies

Full policy engine is out of scope for this stage.

---

## 8) Test cases

`WEB-01` dynamic HTTP route registration from config/discovery.

`WEB-02` request ingress emits payload with correlation metadata.

`WEB-03` success response model routes back to original request waiter.

`WEB-04` error response model routes back with deterministic status mapping.

`WEB-05` websocket ingress/egress uses stream port mapping.

`WEB-06` no direct runtime hardcode of endpoint handlers (discovery-driven only).

`WEB-07` boundary observability events are emitted for ingress and egress.

`WEB-08` timeout/cancel path cleans reply waiters without leaks.

---

## 9) Current status

- Design track started.
- Phase 0 config contract baseline is validated at framework runtime/config level:
  - `runtime.platform.execution_ipc.*`
  - `runtime.platform.process_groups.*`
  - `runtime.web.interfaces.*`
- Runtime builds a normalized contract summary before execution bootstrap.
- Phase 1 secure transport baseline is implemented:
  - secure localhost TCP framing;
  - signed envelope validation (`ts`, `nonce`, `sig`);
  - replay/TTL/kind/payload-size guards.
- Phase 2 runtime integration is completed:
  - DI-bound runtime transport profile service (`memory` / `tcp_local`);
  - lifecycle orchestration extracted to execution module with explicit
    runtime error taxonomy;
  - regression/parity gate passed, including CLI parity with reference outputs.
- Implementation is planned via TDD in the network expansion work plan:
  - [`_work/network_interfaces_expansion_plan`](../_work/network_interfaces_expansion_plan.md)
  - [`_work/web_phase2_stepf_regression_parity_report`](../_work/web_phase2_stepf_regression_parity_report.md)
- Phase 3 bootstrap/secret distribution track is completed through Step F:
  - [`_work/web_phase3_bootstrap_process_and_secret_distribution_tdd_plan`](../_work/web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)
  - [`_work/web_phase3_stepa_contract_freeze_spec`](../_work/web_phase3_stepa_contract_freeze_spec.md)
  - [`_work/web_phase3_stepf_regression_parity_report`](../_work/web_phase3_stepf_regression_parity_report.md)
- Phase 4 correlation scope is completed through Step F:
  - [`_work/web_phase4_reply_correlation_tdd_plan`](../_work/web_phase4_reply_correlation_tdd_plan.md)
  - [`_work/web_phase4_stepf_regression_parity_report`](../_work/web_phase4_stepf_regression_parity_report.md)
- New closure track before FastAPI baseline:
  - [`_work/web_phase4bis_remote_execution_handoff_tdd_plan`](../_work/web_phase4bis_remote_execution_handoff_tdd_plan.md)
  - remaining immediate gap: full remote child-process execution handoff for
    cross-group workloads in `process_supervisor` profile.
- New prerequisite track before Phase 5 implementation:
  - [`_work/web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan`](../_work/web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md)
  - objective: enable real multiprocess supervisor orchestration and Jaeger-ready
    tracing export (OTel primary, OpenTracing bridge for legacy stacks).

---

## 10) Phase 0 config baseline (frozen)

Illustrative shape:

```yaml
runtime:
  platform:
    kv:
      backend: memory
    execution_ipc:
      transport: tcp_local
      bind_host: 127.0.0.1
      bind_port: 0
      auth:
        mode: hmac
        ttl_seconds: 30
        nonce_cache_size: 100000
      max_payload_bytes: 1048576
    process_groups:
      - name: web
      - name: cpu_sync
        stages: [core]
  web:
    interfaces:
      - kind: http
        binds: [request, response]
      - kind: websocket
        binds: [stream]
```
