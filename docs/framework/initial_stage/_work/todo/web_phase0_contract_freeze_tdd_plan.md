# Web Phase 0: contract freeze and validator TDD plan

## Purpose

Lock contracts for web/execution process isolation before implementing transport
and multiprocessing runtime behavior.

This is a focused sub-plan for:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

Current status:

- [x] Validator TDD for `runtime.platform.execution_ipc.*` baseline.
- [x] Validator TDD for `runtime.platform.process_groups.*` baseline.
- [x] Validator TDD for `runtime.web.interfaces.*` baseline.
- [x] Compatibility checks for memory-default profile.
- [x] Runtime contract summary snapshot before execution bootstrap.
- [ ] Envelope security-field schema implementation in transport code (Phase 1+).

---

## Scope of Phase 0

- Runtime config contract freeze:
  - `runtime.platform.execution_ipc.*`
  - `runtime.platform.process_groups.*`
  - `runtime.web.interfaces.*`
- Envelope security field contract freeze (`ts`, `nonce`, `sig`).
- Validator-first implementation (TDD).
- Backward-compatible memory defaults.

Out of scope:

- transport socket implementation;
- process spawning/lifecycle manager;
- FastAPI adapter execution path.

---

## Target config contract (phase baseline)

### `runtime.platform.execution_ipc`

- `transport`: allowed set (phase baseline includes `tcp_local`)
- `bind_host`: must be `127.0.0.1` for local secure profile
- `bind_port`: integer (`0` means ephemeral)
- `auth.mode`: allowed set (phase baseline includes `hmac`)
- `auth.ttl_seconds`: positive integer
- `auth.nonce_cache_size`: positive integer
- `max_payload_bytes`: positive integer

### `runtime.platform.process_groups`

List of group objects with:

- `name`: non-empty, unique
- optional selectors (stage/tag/runner profile references)
- constraints on mutually exclusive selectors (if defined by model)

### `runtime.web.interfaces`

List of interface objects with:

- `kind`: allowed set (HTTP/WS/GraphQL baseline kinds)
- bind and routing declarations under stable framework contracts
- no backend-specific leakage in business contracts

---

## TDD sequence

### Step A — RED tests for IPC config

Add tests in validator suite:

- `CFG-IPC-01`: reject unknown `execution_ipc.transport`.
- `CFG-IPC-02`: reject unknown `auth.mode`.
- `CFG-IPC-03`: reject non-positive `auth.ttl_seconds`.
- `CFG-IPC-04`: reject non-positive `max_payload_bytes`.
- `CFG-IPC-05`: reject non-localhost bind for local profile.

### Step B — RED tests for process groups

- `CFG-PROC-01`: reject malformed `process_groups` structure.
- `CFG-PROC-02`: reject empty group names.
- `CFG-PROC-03`: reject duplicate group names.
- `CFG-PROC-04`: reject unsupported selector fields.

### Step C — RED tests for web interfaces

- `CFG-WEB-01`: reject unknown interface `kind`.
- `CFG-WEB-02`: reject malformed interface entries.
- `CFG-WEB-03`: reject unsupported binds outside stable contracts.

### Step D — RED tests for defaults/compatibility

- `CFG-DEFAULT-01`: absence of new blocks keeps memory profile valid.
- `CFG-DEFAULT-02`: existing deterministic challenge config still validates.

### Step E — GREEN implementation in validator

- Implement minimal strict checks to satisfy Steps A-D.
- Keep diagnostics path-specific and deterministic.
- Do not introduce runtime side effects in validator.

### Step F — REFACTOR

- Factor private helper validation functions by section:
  - `_validate_execution_ipc(...)`
  - `_validate_process_groups(...)`
  - `_validate_web_interfaces(...)`
- Consolidate error formatting conventions.

---

## Documentation sync checklist

- Update references in:
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)
  - [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)
- Mark Phase 0 progress in:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)

---

## Done criteria

- All `CFG-IPC-*`, `CFG-PROC-*`, `CFG-WEB-*`, `CFG-DEFAULT-*` tests pass.
- Validator enforces frozen schema and preserves memory fallback.
- No transport/process implementation code introduced in this phase.
- Docs and plan links are synchronized.
