# Network interfaces expansion plan

## Goal

Add network-facing ingress/egress support (HTTP, WebSocket, HTTP/2 stream, GraphQL)
without leaking protocol details into business nodes.

The target model remains framework-native:

- business nodes work with domain models;
- protocol adapters live in platform layer;
- routing/execution use the same `Envelope` + `Router` + `Runner` mechanics;
- no special-case runtime branches for specific protocols.

## Scope

In scope:

- platform adapters for network protocols;
- mapping protocol payloads to domain models at adapter boundary;
- request/response and stream semantics in one model;
- observability at ingress/egress boundaries.

Out of scope (later stages):

- authN/authZ policy engine;
- distributed backpressure across cluster;
- production-grade retry/DLQ policies for all transports.

## Phases (TDD-first)

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

## TDD test matrix (starter)

`NET-VAL-01` config validator rejects unknown network interface kind.

`NET-VAL-02` config validator rejects interface bind outside stable port set.

`NET-WIRE-01` discovery resolves configured network adapter by name.

`NET-RUN-01` ingress adapter emits domain model, router delivers to consumers.

`NET-RUN-02` node emits response model, response adapter receives exactly once.

`NET-OBS-01` tracing observer records ingress and egress boundary events.

`NET-ERR-01` malformed inbound payload is rejected with monitoring event and no node call.

