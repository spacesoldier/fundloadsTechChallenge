# Execution process port security profile

This note defines how to secure local inter-process communication for the
web-to-execution channel when using TCP on localhost.

Related:

- [Web-execution process isolation and Redis backbone](Web-execution%20process%20isolation%20and%20Redis%20backbone.md)
- [ASGI servers and deployment topologies](ASGI%20servers%20and%20deployment%20topologies.md)
- [FastAPI interface architecture](../FastAPI%20interface%20architecture.md)
- [web_execution_multiprocessing_redis_plan](../../_work/web_execution_multiprocessing_redis_plan.md)
- [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](../../_work/web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)
- [web_phase3_stepa_contract_freeze_spec](../../_work/web_phase3_stepa_contract_freeze_spec.md)

---

## 1) Scope and threat model

This profile protects execution-process ingress when transport is:

- `tcp://127.0.0.1:<ephemeral_port>`

Threat model for this stage:

- untrusted local processes on the same host may try to connect/send data;
- replay of captured local messages is possible;
- malformed/oversized payloads may attempt to exhaust resources.

Out of scope:

- host root compromise;
- network MITM outside localhost path.

---

## 2) Why localhost TCP (and not ipc/inproc)

- `inproc://` is single-process only and does not solve multiprocessing.
- `ipc://` requires filesystem socket path and directory/file permissions.
- `tcp://127.0.0.1:*` works cross-process with no socket-file permission model.

Constraint for this framework stage:

- no extra filesystem permission requirements for local deployment profile.

---

## 3) Baseline hardening controls

### 3.1 Bind policy

- bind execution listener only to `127.0.0.1`;
- use high ephemeral port, not fixed well-known port;
- never bind to `0.0.0.0` in local profile.

### 3.2 Session secret

- web process generates `session_secret` at startup;
- secret is passed to execution process through bootstrap channel only;
- secret is not persisted and is rotated on restart.

### 3.3 Message authentication

Each message includes:

- `ts` (unix time);
- `nonce` (unique random token);
- `payload`;
- `hmac = HMAC(session_secret, canonical(ts, nonce, payload))`.

Receiver must verify:

- HMAC signature;
- timestamp freshness (`abs(now - ts) <= ttl_window`);
- nonce uniqueness (anti-replay cache).

### 3.4 Protocol guards

- strict message schema versioning;
- maximum payload size limit;
- whitelist of allowed message kinds;
- bounded queue/backpressure behavior.

### 3.5 Logging discipline

- never log `session_secret`;
- never log full signed payload in debug by default;
- include only trace-safe diagnostics (`trace_id`, error code, reason).

---

## 4) Envelope contract (security fields)

Minimal envelope extension for local TCP profile:

- `trace_id: str`
- `reply_to: str | null`
- `kind: str`
- `target: str | null`
- `payload_bytes: bytes`
- `headers: map[str, str]`
- `ts: int`
- `nonce: str`
- `sig: str` (HMAC hex/base64)

`payload_bytes` should use deterministic codec (for example msgpack/json bytes)
instead of pickle.

---

## 5) Runtime config direction

Illustrative config keys:

- `runtime.platform.execution_ipc.transport: tcp_local`
- `runtime.platform.execution_ipc.bind_host: 127.0.0.1`
- `runtime.platform.execution_ipc.bind_port: 0` (ephemeral)
- `runtime.platform.execution_ipc.auth.mode: hmac`
- `runtime.platform.execution_ipc.auth.secret_mode: static | generated`
- `runtime.platform.execution_ipc.auth.kdf: none | hkdf_sha256`
- `runtime.platform.execution_ipc.auth.ttl_seconds: <int>`
- `runtime.platform.execution_ipc.auth.nonce_cache_size: <int>`
- `runtime.platform.execution_ipc.max_payload_bytes: <int>`
- `runtime.platform.bootstrap.mode: inline | process_supervisor`

Memory profile remains default when IPC is not enabled.

Phase 3 Step A invariant:

- `runtime.platform.bootstrap.mode=process_supervisor` requires
  `runtime.platform.execution_ipc.transport=tcp_local`.

---

## 6) TDD cases

`SEC-IPC-01` valid signed message is accepted and routed.

`SEC-IPC-02` invalid HMAC is rejected with deterministic error.

`SEC-IPC-03` expired timestamp is rejected.

`SEC-IPC-04` replayed nonce is rejected.

`SEC-IPC-05` oversized payload is rejected before decode.

`SEC-IPC-06` unsupported message kind is rejected.

`SEC-IPC-07` bind policy rejects non-localhost host in local profile.

`SEC-IPC-08` secrets are absent from logs in failure paths.

---

## 7) Migration note

This security profile is transport-agnostic at contract level:

- same envelope auth fields can be applied to Redis/other backends if needed;
- backend swap should not change verification semantics at framework boundary.

Phase status:

- Phase 0 (validator/runtime config contract) is implemented.
- Phase 1 baseline transport enforcement is implemented:
  - signed envelope verification (`sig`);
  - timestamp TTL guard (`ts`);
  - nonce replay guard (`nonce`);
  - message kind whitelist;
  - pre-decode max wire payload guard.
- Phase 2 runtime integration is implemented:
  - transport profile is resolved through DI-bound runtime transport service;
  - lifecycle-managed execution (`start -> ready -> run -> stop`) is enforced;
  - runtime lifecycle/process failures are mapped to explicit error categories.
- Step F regression/parity gate is green:
  - focused runtime transport/lifecycle suites pass;
  - normalized CLI parity vs reference outputs passes for baseline and experiment.
- Phase 3 Step C baseline is implemented:
  - generated/static IPC secret-mode contract is wired in runtime transport;
  - hkdf-based signing-key derivation contract is wired (`kdf=hkdf_sha256`);
  - one-shot bootstrap key-bundle channel contract is available for supervisor->worker handoff;
  - secret representations are redacted from raised diagnostics.
- Phase 3 Step D baseline is implemented:
  - child bootstrap bundle contract is metadata-only (no serialized runtime object graphs);
  - child runtime re-hydrates discovery + DI scope from bundle metadata in-process;
  - child transport binding consumes startup key bundle signing material for parity with parent profile.
- Phase 3 Step E baseline is implemented:
  - graceful stop policy (`graceful_timeout_seconds`, `drain_inflight`) is enforced by supervisor stop contract;
  - timeout fallback triggers deterministic force-terminate path;
  - stop lifecycle events can be emitted once per group with stop mode classification.
- Phase 3 Step F regression/parity gate is green:
  - focused bootstrap/IPC/lifecycle suites pass;
  - Phase 2 compatibility suites pass;
  - memory-profile CLI parity remains exact vs reference outputs for baseline and experiment;
  - tcp-local reject checks remain green for invalid/replay/oversized frames.
- Full cross-process execution topology (multi-process partition and waiter protocol)
  remains in Phase 3+.
