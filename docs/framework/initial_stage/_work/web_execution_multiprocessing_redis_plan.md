# Web-execution multiprocessing + Redis migration plan

Goal: move from in-process execution to isolated web/execution verticals with
Redis-backed platform ports, while preserving deterministic behavior.

Primary references:

- [Web-execution process isolation and Redis backbone](../web/analysis/Web-execution%20process%20isolation%20and%20Redis%20backbone.md)
- [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)
- [Runner loop orchestration](../web/Runner%20loop%20orchestration.md)
- [Detailed rollout plan (secure TCP + process groups + FastAPI)](web_multiprocessing_secure_tcp_fastapi_plan.md)

---

## Stage 0 — Contract freeze + validator prep

- [ ] Freeze backend config keys for queue/kv/reply backends.
- [ ] Add validator tests for `runtime.platform.*.backend` values (`memory|redis`).
- [ ] Keep memory defaults for compatibility.
- [ ] Freeze local execution IPC security profile for multiprocessing mode:
  localhost bind only + session-secret HMAC + timestamp/nonce validation.
- [ ] Add validator tests for execution IPC security keys (ttl, max payload, auth mode).

## Stage 1 — Redis KV adapter (context path first)

- [ ] Add Redis KV adapter behind existing KV contract.
- [ ] Add conformance tests mirrored from in-memory KV tests.
- [ ] Add TTL behavior tests for context keys.

## Stage 2 — Redis Queue adapter

- [ ] Add Redis queue adapter (`push/pop` baseline).
- [ ] Add adapter conformance tests mirrored from in-memory queue tests.
- [ ] Add deterministic ordering characterization tests.

## Stage 3 — Reply channel adapter + correlation service

- [ ] Add reply-channel port and Redis adapter.
- [ ] Add reply timeout/cancel semantics tests.
- [ ] Add waiter cleanup/leak prevention tests.

## Stage 4 — Multiprocessing topology

- [ ] Add profile where web and execution run as separate processes.
- [ ] Add lifecycle tests (startup/shutdown/drain) for both processes.
- [ ] Verify web responsiveness under blocking execution load.
- [ ] Add security tests for local TCP channel:
  invalid signature, replay nonce, expired timestamp, oversized payload.

## Stage 5 — End-to-end parity and fallback safety

- [ ] Run full challenge e2e under memory profile (baseline parity).
- [ ] Run full challenge e2e under Redis profile (output parity).
- [ ] Keep in-memory profile as fallback and CI baseline.

## Stage 6 — Observability for distributed mode

- [ ] Emit queue lag, timeout, redelivery diagnostics through observability service.
- [ ] Add test assertions for boundary events in Redis profile.
