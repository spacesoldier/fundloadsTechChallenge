# Web Phase 3: bootstrap process and secret distribution TDD plan

## Purpose

Introduce real process-isolated execution groups with:

- runtime-generated IPC secrets;
- explicit bootstrap supervisor process mode;
- deterministic graceful-stop protocol;
- discovery/DI bootstrap in child processes without runtime hardcode.

This plan is a detailed Phase 3 checklist for:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- [network_interfaces_expansion_plan](network_interfaces_expansion_plan.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

---

## Current status

- [x] Step A complete: config/runtime-summary contract is frozen and test-covered.
- [x] Step B complete: bootstrap supervisor API and process-supervisor runtime path are integrated.
- [x] Step C complete: generated/static key material and one-shot bootstrap-channel delivery are integrated.
- [x] Step D complete: child runtime metadata bundle and DI re-hydration contract are integrated.
- [x] Step E complete: graceful stop protocol with timeout fallback and stop-event emission is integrated.
- [x] Step F complete: regression/parity gate executed and documented.

Phase 3 status: complete.

Step A spec:

- [web_phase3_stepa_contract_freeze_spec](web_phase3_stepa_contract_freeze_spec.md)

Step A acceptance evidence:

- validator contract tests are green (`BOOT-CFG-01..05`);
- runtime summary contract tests are green (`BOOT-SUM-01..02`);
- cross-plan links are synchronized in:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
  - [network_interfaces_expansion_plan](network_interfaces_expansion_plan.md)
  - [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

Step B acceptance evidence:

- bootstrap supervisor contract introduced in platform services:
  - `BootstrapSupervisor.start_groups(group_names)`
  - `BootstrapSupervisor.wait_ready(timeout_seconds)`
  - `BootstrapSupervisor.stop_groups(graceful_timeout_seconds, drain_inflight)`
- runtime execution path switch is explicit:
  - `bootstrap.mode=process_supervisor` -> supervisor path
  - otherwise (`tcp_local`) -> existing runtime lifecycle path
- deterministic start-order and start-failure behavior are test-covered:
  - `BOOT-API-01`: process-supervisor lifecycle wraps runner (`start_groups -> wait_ready -> run -> stop_groups`)
  - `BOOT-API-02`: `process_groups` order from config is preserved
  - `BOOT-API-03`: start failure maps to deterministic runtime bootstrap error category

Step C acceptance evidence:

- key material resolver supports both secret modes:
  - `secret_mode=generated` -> startup-generated non-empty bytes (`KEY-IPC-01`)
  - `secret_mode=static` -> explicit static secret remains supported (`KEY-IPC-02`)
- kdf profile is applied to signing secret:
  - `kdf=none` keeps master secret;
  - `kdf=hkdf_sha256` derives deterministic signing key from startup master.
- one-shot bootstrap channel is implemented and wired:
  - bundle publish once, consume once (`KEY-IPC-03`);
  - process-supervisor path prepares and passes channel via optional loader hook.
- secret leakage guard is test-covered:
  - invalid secret configuration errors do not include secret representations (`KEY-IPC-04`).

Step D acceptance evidence:

- child bootstrap API introduced:
  - `ChildBootstrapBundle` (metadata only);
  - `bootstrap_child_runtime_from_bundle(...)` builds child runtime from bundle.
- metadata contract is wired into process-supervisor orchestration:
  - optional supervisor hook `load_child_bootstrap_bundle(bundle)` receives metadata bundle;
  - bundle contains discovery roots, runtime slice, scenario/process-group metadata, key bundle.
- child runtime re-hydrates discovery and DI in child scope:
  - modules loaded from bundle discovery roots + platform discovery modules;
  - child `ApplicationContext` and `ConsumerRegistry` are created in-process;
  - runtime transport bindings use key bundle signing material;
  - child resolves `RuntimeTransportService` and `RuntimeLifecycleManager` from DI.
- malformed bundle rejection is deterministic:
  - invalid discovery modules, runtime shape, or missing key bundle raise explicit child-bootstrap error category (`CHILD-BOOT-03`).

Step E acceptance evidence:

- graceful stop contract is enforced:
  - supervisor `stop_groups(graceful_timeout_seconds, drain_inflight)` receives lifecycle policy values (`STOP-IPC-01`);
  - successful graceful stop does not trigger force-terminate fallback.
- timeout fallback contract is enforced:
  - timeout (`TimeoutError` or explicit `False` stop result) triggers `force_terminate_groups(group_names)` (`STOP-IPC-02`);
  - missing fallback hook raises deterministic timeout category;
  - fallback failure raises deterministic runtime stop category.
- stop lifecycle events are emitted once per group:
  - optional hook `emit_stop_event(group_name, mode)` receives exactly one event per group (`STOP-IPC-03`);
  - mode reflects actual stop path (`graceful` or `forced`).

Step F report:

- [web_phase3_stepf_regression_parity_report](web_phase3_stepf_regression_parity_report.md)

Step F acceptance evidence:

- focused process-bootstrap suites executed and green;
- Phase 2 IPC/lifecycle backward-compat suites executed and green;
- memory-profile CLI parity (baseline + experiment) remains exact vs reference outputs;
- tcp-local reject behavior for invalid/replay/oversized frames remains green.

---

## Scope of Phase 3

In scope:

- bootstrap orchestration mode (`inline` vs `process_supervisor`);
- one-shot secure secret distribution to worker groups;
- process-group worker startup/ready/stop contracts;
- child-runtime discovery/DI bootstrap contract.

Out of scope:

- request/reply waiter protocol across process boundary (Phase 4);
- FastAPI adapter runtime wiring (Phase 5);
- Redis backend parity (Phase 8).

---

## Target contracts

### 1) Secret lifecycle contract

- runtime may use generated secret mode:
  - `runtime.platform.execution_ipc.auth.secret_mode: generated`
- generated secret must be bytes and never printed in diagnostics.
- optional key derivation profile:
  - `runtime.platform.execution_ipc.auth.kdf: hkdf_sha256`
  - directional keys (`web->exec`, `exec->web`) derived from one master seed.

### 2) Bootstrap process contract

- `runtime.platform.bootstrap.mode`:
  - `inline` (existing single-process fallback),
  - `process_supervisor` (new multiprocess mode).
- bootstrap supervisor responsibilities:
  - validate runtime contract;
  - generate/derive runtime transport secrets;
  - start worker groups;
  - pass bootstrap bundle to children over local one-shot control channel;
  - enforce start/ready/stop lifecycle.

### 3) Graceful stop contract

- stop command includes:
  - `graceful_timeout_seconds`
  - `drain_inflight`
- worker stop sequence:
  1. stop accepting new ingress;
  2. drain in-flight units;
  3. flush observability/sinks;
  4. acknowledge `stopped`.
- supervisor fallback:
  - if no ack until timeout -> deterministic forced terminate path.

### 4) Child bootstrap contract (code + DI)

- child process receives metadata bundle, not object graphs:
  - discovery module roots;
  - process-group selectors;
  - runtime profile slice;
  - one-shot transport key material.
- child builds its own:
  - module imports from existing `PYTHONPATH`,
  - `ApplicationContext` discovery,
  - DI scope and runtime services.
- no pickled node/service instances transported between processes.

---

## TDD sequence

### Step A — config and contract freeze (RED)

- `BOOT-CFG-01`: reject unsupported `runtime.platform.bootstrap.mode`.
- `BOOT-CFG-02`: reject invalid `auth.secret_mode`.
- `BOOT-CFG-03`: reject invalid `auth.kdf`.
- `BOOT-CFG-04`: enforce bootstrap mode defaults (`inline`).
- `BOOT-CFG-05`: `process_supervisor` requires `execution_ipc.transport=tcp_local`.

Step A completion note:

- validator now normalizes/validates:
  - `runtime.platform.bootstrap.mode`
  - `runtime.platform.execution_ipc.auth.secret_mode`
  - `runtime.platform.execution_ipc.auth.kdf`
- runtime summary now exposes bootstrap/secret contract fields.

### Step B — bootstrap supervisor API (RED)

- `BOOT-API-01`: supervisor exposes `start_groups()`, `wait_ready()`, `stop_groups()`.
- `BOOT-API-02`: deterministic group start order from config.
- `BOOT-API-03`: deterministic error on worker start failure.

### Step C — generated secret and one-shot distribution (RED)

- `KEY-IPC-01`: generated secret mode produces non-empty random bytes.
- `KEY-IPC-02`: static secret mode remains supported for local/dev profile.
- `KEY-IPC-03`: one-shot channel delivers bootstrap key bundle to child.
- `KEY-IPC-04`: secret material is redacted in all raised errors/logs.

### Step D — child runtime bootstrap and DI re-hydration (GREEN)

- `CHILD-BOOT-01`: child process builds discovery/DI from bundle metadata.
- `CHILD-BOOT-02`: child resolves runtime transport service and lifecycle service from DI.
- `CHILD-BOOT-03`: child rejects malformed bootstrap bundle deterministically.

### Step E — graceful stop protocol (GREEN)

- `STOP-IPC-01`: graceful stop drains in-flight work within timeout.
- `STOP-IPC-02`: stop timeout triggers deterministic forced terminate path.
- `STOP-IPC-03`: lifecycle stop events are emitted once per group.

### Step F — regression/parity gate

- run focused process-bootstrap suites;
- run existing Phase 2 suites (IPC/lifecycle) for backward compatibility;
- verify memory profile parity remains unchanged;
- verify tcp-local profile still rejects invalid/replay/oversized frames.

---

## Implementation notes

- Prefer local control channel (`Pipe` or `socketpair`) for one-shot bootstrap bundle.
- Do not use shared-memory named segments for key material in this phase.
- Keep `inline` mode as deterministic fallback for existing test and CLI flows.
- Treat bootstrap supervisor as platform service boundary, not app-specific runtime branch.

---

## Documentation sync checklist

- Update after each Step:
  - [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md) Phase 3 progress;
  - [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md);
  - [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md) status note.

---

## Done criteria

- bootstrap process mode is test-covered and deterministic;
- generated secret distribution is implemented without secret leakage;
- graceful stop semantics are enforced and test-covered;
- child runtime discovery/DI bootstrap works without serialized object graphs;
- Phase 2 regression and memory-profile parity remain green.
