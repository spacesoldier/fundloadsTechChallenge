# Web multiprocessing + secure TCP + FastAPI plan

## Goal

Move from in-process execution to a process-isolated web/execution model with:

- secure localhost TCP communication;
- framework-native process group placement for nodes;
- discovery/DI-driven runtime wiring (no manual runtime helpers);
- FastAPI interfaces (HTTP, WebSocket, streaming, GraphQL) on top of the same core contracts.

This plan extends and details:

- [network interfaces expansion plan](network_interfaces_expansion_plan.md)
- [web_execution_multiprocessing_redis_plan](web_execution_multiprocessing_redis_plan.md)
- [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)
- [engine runner/router target model plan](engine_runner_router_target_model_tdd_plan.md)
- [engine runtime contract cleanup plan](engine_runtime_contract_cleanup_tdd_plan.md)
- [web phase 5pre multiprocess supervisor and observability plan](web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md)
- [multiprocess bus ownership and outbound route cache analysis](../web/analysis/Multiprocess%20bus%20ownership%20and%20outbound%20route%20cache.md)

---

## Guiding constraints

- TDD-first for each phase (red -> green -> refactor).
- No project-specific runtime branches in framework core.
- Stable platform contracts first, backend implementation second.
- Memory profile remains default fallback for local deterministic runs.
- Security is part of contract, not an optional afterthought.

---

## Phase 0 — Contract freeze and config schema

Detailed execution checklist:

- [web_phase0_contract_freeze_tdd_plan](web_phase0_contract_freeze_tdd_plan.md)

Progress note:

- Validator-level contract checks are implemented and covered by tests.
- Runtime contract summary bootstrap checks are implemented.
- Transport-level envelope security fields remain to be implemented in Phase 1.

### Objective

Freeze the config and envelope contracts before coding transport/process logic.

### Deliverables

- Runtime config schema for:
  - `runtime.platform.execution_ipc.*`
  - `runtime.platform.process_groups.*`
  - `runtime.web.interfaces.*`
- Envelope schema revision with security metadata fields.
- Explicit compatibility note for memory-only profile.

### TDD cases

- `CFG-IPC-01` reject unknown `execution_ipc.transport`.
- `CFG-IPC-02` reject invalid `auth.mode`.
- `CFG-IPC-03` reject non-positive `ttl_seconds` / `max_payload_bytes`.
- `CFG-PROC-01` reject malformed process group declarations.
- `CFG-WEB-01` reject unsupported web interface kind.
- `CFG-DEFAULT-01` memory profile defaults remain stable.

---

## Phase 1 — Secure localhost TCP transport adapter

Detailed execution checklist:

- [web_phase1_secure_tcp_transport_tdd_plan](web_phase1_secure_tcp_transport_tdd_plan.md)

Progress note:

- Secure TCP transport baseline is implemented (`secure_tcp_transport`) with
  HMAC/TTL/replay/kind/max-size guards.
- `SEC-IPC` Phase 1 test suite is added; socket roundtrip test is environment-dependent.
- Phase 0 contract freeze for config and runtime summary is completed.

### Objective

Implement process-to-process channel via `tcp://127.0.0.1:<ephemeral>` with
message authentication and replay protection.

### Deliverables

- Transport adapter for send/receive over localhost TCP.
- HMAC validation layer (`ts`, `nonce`, `sig`).
- TTL window + nonce cache.
- Payload size and kind guards.

### TDD cases

- `SEC-IPC-01` valid signed message accepted.
- `SEC-IPC-02` invalid signature rejected.
- `SEC-IPC-03` expired timestamp rejected.
- `SEC-IPC-04` replay nonce rejected.
- `SEC-IPC-05` oversized payload rejected pre-decode.
- `SEC-IPC-06` unsupported kind rejected.
- `SEC-IPC-07` non-localhost bind rejected in local profile.

---

## Phase 2 — Process lifecycle manager

Detailed execution checklist:

- [web_phase2_secure_tcp_runtime_integration_tdd_plan](web_phase2_secure_tcp_runtime_integration_tdd_plan.md)

Progress note:

- Phase 2 is complete.
- Step A complete: runtime transport profile wiring and summary are integrated.
- Step B complete: lifecycle startup/ready/stop/crash contract is integrated and tested.
- Step C complete: tcp_local queue boundary enforces signed-frame safety checks (`IPC-INT-05..08`).
- Step D complete: runtime transport is DI-bound as `RuntimeTransportService` (`IPC-INT-09..12`).
- Step E complete: lifecycle orchestration moved out of builder to execution module with
  explicit runtime error classes (`PROC-INT-05..08`).
- Step F complete: regression/parity gate is green, including CLI parity with reference
  outputs (`baseline` + `exp_mp`).
- Step F report:
  [web_phase2_stepf_regression_parity_report](web_phase2_stepf_regression_parity_report.md)

### Objective

Provide framework-native startup/shutdown supervision for execution processes.

### Deliverables

- Platform service for worker process lifecycle.
- Health/ready state reporting.
- Graceful shutdown and drain behavior.

### TDD cases

- `PROC-01` deterministic start order (services before workers).
- `PROC-02` graceful shutdown drains in-flight workload.
- `PROC-03` abnormal worker termination surfaces deterministic error.
- `PROC-04` restart policy behavior is explicit and test-covered.

---

## Phase 3 — Process group placement model

Detailed execution checklist:

- [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)

Progress note:

- Phase 3 started.
- Step A complete (contract freeze):
  [web_phase3_stepa_contract_freeze_spec](web_phase3_stepa_contract_freeze_spec.md)
- Step B complete (bootstrap supervisor API):
  - process-supervisor runtime path now calls `start_groups -> wait_ready -> run -> stop_groups`;
  - start-order is deterministic from `runtime.platform.process_groups`;
  - start failure is mapped to deterministic runtime bootstrap error category.
- Step C complete (generated secret + one-shot bootstrap bundle):
  - runtime key material resolver supports `secret_mode=generated|static`;
  - signing key derivation supports `kdf=none|hkdf_sha256`;
  - one-shot bootstrap bundle channel contract is wired into process-supervisor path;
  - secret values are redacted from raised diagnostics.
- Step D complete (child discovery/DI bootstrap):
  - metadata-only child bootstrap bundle contract is introduced and passed to supervisor hook;
  - child runtime bootstrap re-hydrates discovery, `ApplicationContext`, DI scope and runtime services;
  - child runtime transport binding consumes key bundle signing material;
  - malformed child bootstrap bundles fail with deterministic bootstrap error category.
- Step E complete (graceful stop protocol):
  - graceful stop policy is enforced through supervisor stop contract;
  - timeout path triggers deterministic force-terminate fallback;
  - fallback errors are mapped to explicit runtime stop categories;
  - lifecycle stop events are emitted once per group with path mode (`graceful`/`forced`).
- Step F complete (regression/parity gate):
  - focused process-bootstrap suites are green;
  - Phase 2 IPC/lifecycle backward-compat suites are green;
  - memory-profile CLI parity vs reference outputs is unchanged (`baseline` + `exp_mp`);
  - tcp-local reject guards remain green for invalid/replay/oversized frames.
- Step F report:
  [web_phase3_stepf_regression_parity_report](web_phase3_stepf_regression_parity_report.md)
- Step A frozen fields:
  - `runtime.platform.bootstrap.mode` (`inline | process_supervisor`)
  - `runtime.platform.execution_ipc.auth.secret_mode` (`static | generated`)
  - `runtime.platform.execution_ipc.auth.kdf` (`none | hkdf_sha256`)
  - invariant: `process_supervisor` requires `execution_ipc.transport=tcp_local`
- Next implementation track:
  - Phase 4 (routing/reply across process boundaries).

### Objective

Allow node groups to execute in different processes (web, sync CPU, async IO, etc.).

### Deliverables

- Placement contract for `process_groups`.
- DAG partitioning by stage/tag/runner profile.
- Cross-group edge transformation to transport hops.

### TDD cases

- `PLACEMENT-01` node assigned to configured group by rule.
- `PLACEMENT-02` unknown group reference fails preflight.
- `PLACEMENT-03` cross-group edge inserts IPC hop.
- `PLACEMENT-04` local-only edge stays in-process.
- `BOOT-API-01` supervisor start/ready/stop lifecycle is deterministic.
- `KEY-IPC-01` generated key distribution is one-shot and redacted in diagnostics.
- `STOP-IPC-01` graceful drain/timeout stop policy is enforced.

---

## Phase 4 — Routing/reply across process boundaries

Detailed execution checklist:

- [web_phase4_reply_correlation_tdd_plan](web_phase4_reply_correlation_tdd_plan.md)

Progress note:

- Phase 4 review completed.
- Contract/runtime correlation path is now active through Step D:
  - waiter contract + in-memory implementation;
  - ingress registration + terminal completion wiring;
  - boundary-first execution path for `process_supervisor`.
- Phase 4 Step A is executed as RED:
  - reply waiter contract is frozen in platform services;
  - `REPLY-01..05` tests are added and currently fail as expected.
- Phase 4 Step B is executed as GREEN:
  - in-memory `ReplyWaiterService` implementation is added;
  - `REPLY-01..05` tests are green.
- Phase 4 Step C is executed as GREEN:
  - runner ingress path registers waiters for `Envelope(reply_to=...)`;
  - terminal events complete waiters by correlation trace id;
  - reply metadata persistence/propagation is covered by tests.
- Phase 4 Step D is executed as GREEN:
  - bootstrap supervisor boundary execution hook is introduced and wired into lifecycle path;
  - boundary terminal envelopes complete correlated waiters by trace id;
  - process-supervisor path now prefers boundary execution API over direct local callback when available.
- Phase 4 Step E is executed as GREEN:
  - waiter service exposes deterministic counters for timeout/cancel/late-drop lifecycle;
  - sanitized diagnostics events are bounded and do not expose raw reason/payload/error data;
  - Step-E focused reply-waiter tests are green.
- Phase 4 Step F is executed as GREEN:
  - correlation-focused, compatibility, and deterministic integration suites are green;
  - memory-profile parity remains exact for baseline and experiment outputs;
  - tcp-local reject guard behavior remains green.
- Phase 4 detailed closure report:
  - [web_phase4_stepf_regression_parity_report](web_phase4_stepf_regression_parity_report.md)
- Phase 4 residual scope:
  - full remote child-process execution handoff is tracked as Phase 4bis:
    [web_phase4bis_remote_execution_handoff_tdd_plan](web_phase4bis_remote_execution_handoff_tdd_plan.md)

### Objective

Preserve request/reply correlation and deterministic egress with separated processes.

### Deliverables

- Reply waiter service for web process.
- Correlation protocol for terminal success/error models.
- Timeout/cancel cleanup semantics.

### TDD cases

- `REPLY-01` request roundtrip returns to original waiter.
- `REPLY-02` timeout cleans waiter without leaks.
- `REPLY-03` duplicate terminal event handled deterministically.
- `REPLY-04` cancellation path emits predictable outcome.

---

## Phase 4bis — Remote execution handoff activation

Detailed execution checklist:

- [web_phase4bis_remote_execution_handoff_tdd_plan](web_phase4bis_remote_execution_handoff_tdd_plan.md)

Progress note:

- Correlation/reply reliability from Phase 4 is complete.
- Remaining closure need before FastAPI baseline: activate real child-process
  execution handoff for cross-group workloads.
- Phase 4bis started.
- Step A is executed as RED:
  - handoff contract suite is added (`HANDOFF-01..05`);
  - all 5 tests fail as expected against current runtime behavior;
  - detailed RED evidence is documented in:
    [web_phase4bis_remote_execution_handoff_tdd_plan](web_phase4bis_remote_execution_handoff_tdd_plan.md)
- Step B is executed as GREEN:
  - parent process-supervisor path now builds explicit boundary dispatch inputs;
  - single-group profile stays local, multi-group profile routes through boundary dispatch;
  - child-trace terminal aliases complete parent waiter correlation;
  - boundary drain hook is invoked before stop when supervisor exposes it.
- Step C is executed as GREEN:
  - child consume->execute->emit loop is wired via child bootstrap runtime API;
  - supervisor boundary path can delegate execution to child loop using loaded child bundle;
  - child runtime resolves discovered node targets and emits boundary outputs deterministically.
- Step D is executed as GREEN:
  - parent reply completion is fully integrated with child-terminal boundary outputs;
  - duplicate child terminals complete once and are counted deterministically (`duplicate_terminal`);
  - late child terminals without waiter are dropped deterministically (`late_reply_drop`);
  - contract evidence is fixed by `HANDOFF-D-01..02` in:
    `tests/stream_kernel/execution/test_remote_handoff_contract.py`.
- Step E is executed as GREEN:
  - remote handoff failures are mapped to deterministic categories (`timeout`, `transport`, `execution`);
  - sanitized handoff diagnostics hook is available via supervisor contract (`emit_handoff_failure`);
  - primary boundary failure is not masked by shutdown fallback errors (shutdown diagnostics are still emitted);
  - contract evidence is fixed by `HANDOFF-E-01..03` in:
    `tests/stream_kernel/execution/test_remote_handoff_contract.py`.
- Step F is executed as GREEN:
  - focused handoff suites are green;
  - Phase 2/3/4 compatibility suites are green;
  - memory-profile parity remains exact for baseline + experiment outputs (`jq -cS` + `diff -u`);
  - tcp-local reject guards remain green.
- Phase 4bis closure report:
  - [web_phase4bis_stepf_regression_parity_report](web_phase4bis_stepf_regression_parity_report.md)

### Phase 4bis follow-up (engine responsibilities cleanup)

Before expanding web interfaces (Phase 5), runtime engine responsibilities are
consolidated using:

- [engine runner/router target model plan](engine_runner_router_target_model_tdd_plan.md)
- [engine runtime contract cleanup plan](engine_runtime_contract_cleanup_tdd_plan.md)

Scope:

- move request/reply correlation out of `SyncRunner`;
- make routing contract explicit for local/boundary/terminal outcomes;
- switch boundary dispatch to placement-driven grouping;
- align child invocation path with parent DI/context execution model.
- enforce strict runtime allow-list schema and remove legacy runtime special-casing;
- rename routing facade contract from `RoutingPort` to `RoutingService`;
- remove compatibility tails from runner/routing/lifecycle hot paths.

### Objective

Activate real process-isolated execution for cross-group workloads in
`process_supervisor` mode.

### Deliverables

- Deterministic DAG partition mapping to `process_groups`.
- Boundary dispatcher that forwards execution to child process runtime.
- Child execution loop receives work over secure transport and emits terminal outputs.
- Correlated terminal responses return through boundary path.
- `memory` profile remains behavior-compatible fallback.

### TDD cases

- `HANDOFF-01` cross-group node executes in designated child group runtime.
- `HANDOFF-02` in-group node stays local (no unnecessary transport hop).
- `HANDOFF-03` boundary terminal from child completes waiter by original trace id.
- `HANDOFF-04` child crash/timeout maps to deterministic runtime worker error.
- `HANDOFF-05` graceful drain preserves in-flight delivery semantics across groups.

---

## Phase 5 — FastAPI interface baseline (discovery-driven)

Phase 5 prerequisite:

- [web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan](web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md)
- Phase 5 starts only after 5pre exit criteria are green (real multiprocess
  supervisor + OTel/OpenTracing observability substrate).

### Objective

Attach FastAPI HTTP/websocket endpoints as platform adapters using the same routing core.

### Deliverables

- Programmatic route/websocket registration from config+discovery.
- Ingress adapters emit framework envelope.
- Egress adapters resolve correlated responses.

### TDD cases

- `WEB-01` dynamic HTTP route registration.
- `WEB-02` dynamic websocket route registration.
- `WEB-03` ingress attaches correlation metadata.
- `WEB-04` terminal response delivered to original caller.

---

## Phase 6 — Streaming, HTTP/2 and GraphQL layer

### Objective

Expand interface capabilities without changing business-node/runtime contracts.

### Deliverables

- Streaming response adapter path.
- HTTP/2 profile support (server-dependent).
- GraphQL adapter integration (query/mutation baseline).

### TDD cases

- `WEB-STREAM-01` streaming egress maps to stream contract.
- `WEB-H2-01` HTTP/2 profile config validated and loaded.
- `WEB-GQL-01` GraphQL query roundtrip through framework contracts.
- `WEB-GQL-02` GraphQL mutation error mapping deterministic.

---

## Phase 7 — Observability for distributed mode

### Objective

Make cross-process behavior diagnosable through framework observability contracts.

### Deliverables

- Boundary events for IPC ingress/egress.
- Metrics/events for rejects, timeouts, lag, redeliveries.
- No secret leakage in emitted diagnostics.

### TDD cases

- `OBS-IPC-01` invalid signature generates security event.
- `OBS-IPC-02` timeout/replay events emitted.
- `OBS-IPC-03` queue lag metric emitted under load.
- `OBS-IPC-04` secret fields absent from logs/events.

---

## Phase 8 — Backend matrix and fallback parity

### Objective

Ensure behavior parity across memory and distributed profiles.

### Deliverables

- Memory profile (single-process fallback) unchanged.
- Multiprocessing secure-TCP profile stable.
- Redis-backed profile parity path preserved.

### TDD cases

- `PARITY-01` baseline output parity: memory vs secure-TCP profile.
- `PARITY-02` baseline output parity: secure-TCP vs Redis profile.
- `PARITY-03` deterministic sink ordering parity in all profiles.

---

## Phase 9 — Hardening and rollout gates

### Objective

Define minimal production-readiness gates before enabling web track by default.

### Deliverables

- Preflight checks for security/process/interface config.
- Startup diagnostics summary (active interfaces, process groups, transport profile).
- Failure matrix documented (timeouts, worker crash, invalid messages).

### TDD cases

- `GATE-01` preflight fails on insecure/malformed IPC config.
- `GATE-02` startup summary contains active profile and groups.
- `GATE-03` controlled failure scenarios produce deterministic exit/reporting.

---

## Dependency order

1. Phase 0 -> 1 -> 2 -> 3 (core process/transport substrate)
2. Phase 4 (correlated reply reliability)
3. Phase 4bis (real remote execution handoff)
4. Phase 5pre (real multiprocess supervisor + OTel/OpenTracing substrate)
5. Phase 5 -> 6 (web capability expansion)
6. Phase 7 -> 8 -> 9 (observability, parity, hardening)

---

## Immediate next step (execution)

- Start Phase 5pre Step A:
  - freeze config/contracts for multiprocess supervisor control plane;
  - add RED validator and supervisor contract tests;
  - lock observability exporter contract (`otel_otlp`, `opentracing_bridge`).

Follow-up track (after Phase 5pre closure):

- introduce dedicated data-plane bus backend (ZeroMQ local profile);
- keep star control plane for lifecycle only;
- add routing-service outbound bus cache with versioned invalidation.
