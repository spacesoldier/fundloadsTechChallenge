# Web Phase 1: secure localhost TCP transport TDD plan

## Purpose

Implement secure local inter-process transport for web <-> execution channel
using localhost TCP, with deterministic framework contracts.

This is a focused sub-plan for:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

Current status:

- [x] RED tests for secure TCP happy path and guard rails.
- [x] GREEN transport adapter implementation with HMAC + TTL + replay checks.
- [x] REFACTOR for canonical serializer/signature/replay helper boundaries.
- [x] Documentation sync in web architecture and security profile docs.

---

## Scope of Phase 1

- Transport-level message envelope security fields:
  - `ts`, `nonce`, `sig`
- Localhost TCP adapter baseline:
  - send/receive for framework envelope payload bytes
- Security checks at receive boundary:
  - signature verification
  - TTL validation
  - replay protection (nonce cache)
  - kind whitelist
  - max payload size guard

Out of scope:

- process lifecycle manager (Phase 2)
- process-group DAG placement (Phase 3)
- full request/reply orchestration across processes (Phase 4)

---

## Target transport contract (phase baseline)

Runtime config dependencies used by this phase:

- `runtime.platform.execution_ipc.transport = tcp_local`
- `runtime.platform.execution_ipc.bind_host = 127.0.0.1`
- `runtime.platform.execution_ipc.bind_port` (allow `0` for ephemeral)
- `runtime.platform.execution_ipc.auth.mode = hmac`
- `runtime.platform.execution_ipc.auth.ttl_seconds > 0`
- `runtime.platform.execution_ipc.auth.nonce_cache_size > 0`
- `runtime.platform.execution_ipc.max_payload_bytes > 0`

### Secure envelope fields

- `trace_id: str | None`
- `reply_to: str | None` (optional for future reply-channel use)
- `kind: str`
- `target: str | list[str] | None`
- `payload_bytes: bytes`
- `headers: dict[str, str]`
- `ts: int` (unix timestamp)
- `nonce: str`
- `sig: str` (HMAC)

### Validation rules

- `payload_bytes` length must be `<= max_payload_bytes`
- `kind` must be in allowed set
- `abs(now - ts) <= ttl_seconds`
- `(nonce, ts_window)` must be unique in replay cache
- `sig` must match canonical payload signature

### Binding policy

- local profile: bind/listen only on `127.0.0.1`
- `bind_port=0` allowed (ephemeral)

---

## TDD sequence

### Step A — RED tests: happy path

Add tests for baseline success:

- `SEC-IPC-01`: valid signed message accepted.
- `SEC-IPC-01b`: send/receive roundtrip preserves payload bytes and metadata.

### Step B — RED tests: signature and replay

- `SEC-IPC-02`: invalid signature rejected.
- `SEC-IPC-02b`: missing signature rejected.
- `SEC-IPC-04`: replay nonce rejected.

### Step C — RED tests: freshness and limits

- `SEC-IPC-03`: expired timestamp rejected.
- `SEC-IPC-05`: oversized payload rejected before decode.
- `SEC-IPC-06`: unsupported message kind rejected.

### Step D — RED tests: bind and config policy

- `SEC-IPC-07`: non-localhost bind rejected in local profile.
- `SEC-IPC-07b`: invalid bind port rejected by transport setup.

### Step E — GREEN implementation

Implement minimal secure transport adapter + verifier:

- deterministic canonical signing function
- HMAC verify helper
- nonce cache abstraction (in-memory baseline)
- secure decode pipeline with fail-fast guards

No runtime monkeypatch points, no project-specific shortcuts.

### Step F — REFACTOR

- Extract pure helpers:
  - canonical payload builder
  - signer/verifier
  - replay guard
  - envelope serializer/deserializer
- Normalize errors to stable categories for observability in Phase 7.

---

## Documentation sync checklist

- Update:
  - [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md) (status section)
- Mark Phase 1 progress in:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

---

## Done criteria

- All `SEC-IPC-*` Phase 1 tests pass.
- Transport enforces signature/TTL/replay/size/kind policies.
- Local profile bind policy is strictly localhost.
- No process-manager logic introduced yet.
- Existing memory profile tests remain green.
