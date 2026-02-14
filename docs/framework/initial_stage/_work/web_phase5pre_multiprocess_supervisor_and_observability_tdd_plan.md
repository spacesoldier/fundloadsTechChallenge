# Web Phase 5pre: multiprocess supervisor and observability substrate (TDD plan)

## Goal

Before implementing FastAPI endpoints (Phase 5), complete the missing runtime
substrate:

1. real multiprocess execution for process groups (not local fallback);
2. engine-native control plane for worker lifecycle and IPC bootstrap;
3. framework-native observability adapters for OpenTelemetry and OpenTracing bridge;
4. deterministic tracing continuity across process boundaries.

This phase is a hard prerequisite for:

- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md) Phase 5;
- [FastAPI interface architecture](../web/FastAPI%20interface%20architecture.md);
- [network_interfaces_expansion_plan](network_interfaces_expansion_plan.md).
- Step A detailed contract freeze spec:
  [web_phase5pre_stepa_contract_freeze_spec](web_phase5pre_stepa_contract_freeze_spec.md)
- Step B detailed multiprocess supervisor RED spec:
  [web_phase5pre_stepb_multiprocess_supervisor_red_spec](web_phase5pre_stepb_multiprocess_supervisor_red_spec.md)
- Step C detailed control-plane TDD spec:
  [web_phase5pre_stepc_control_plane_tdd_spec](web_phase5pre_stepc_control_plane_tdd_spec.md)
- Step D detailed lifecycle supervisor TDD spec:
  [web_phase5pre_stepd_multiprocess_lifecycle_tdd_spec](web_phase5pre_stepd_multiprocess_lifecycle_tdd_spec.md)
- Step E detailed boundary delegation TDD spec:
  [web_phase5pre_stepe_boundary_delegation_tdd_spec](web_phase5pre_stepe_boundary_delegation_tdd_spec.md)
- Step F detailed observability adapters TDD spec:
  [web_phase5pre_stepf_observability_adapters_tdd_spec](web_phase5pre_stepf_observability_adapters_tdd_spec.md)
- Step G detailed smoke topology TDD spec:
  [web_phase5pre_stepg_smoke_topology_tdd_spec](web_phase5pre_stepg_smoke_topology_tdd_spec.md)
- Follow-up handoff/OTLP/Jaeger closure plan:
  [web_phase5pre_handoff_otlp_jaeger_tdd_plan](web_phase5pre_handoff_otlp_jaeger_tdd_plan.md)

---

## Why now

Current state:

- process-group contracts and secure tcp transport are implemented;
- routing/reply and remote handoff contracts are implemented;
- process-supervisor mode still relies on local in-process supervisor baseline.

Gap:

- no real OS worker process pool is started for `runtime.platform.process_groups`;
- no production-like orchestration path to validate web/execution split behavior;
- tracing is available via JSONL/stdout but not exported to Jaeger-compatible pipeline.

---

## Scope

In scope:

- multiprocess supervisor implementation (`spawn`-based baseline);
- worker control plane over framework adapters/services;
- bootstrap metadata and key exchange path over explicit control channel;
- worker lifecycle states (`starting`, `ready`, `running`, `stopping`, `stopped`, `failed`);
- lifecycle logging for supervisor/workers (stdout baseline + structured events);
- readiness-gated start of workload processing (no source ingestion before READY);
- OTel trace adapter and OpenTracing compatibility bridge adapter;
- trace propagation and parent/child correlation over transport envelopes.

### Phase 5pre follow-up closure (February 13, 2026)

- [x] Closed child-bootstrap gap for runtime-generated graph-native source/sink nodes:
  - child bootstrap bundle now carries adapter config;
  - child runtime reconstructs `source:*` / `sink:*` wrappers via the same builder rails;
  - child boundary execution routes non-targeted outputs through `RoutingService`.
- [x] Added route markers for cross-process tracing continuity:
  - boundary inputs carry `source_group` and `route_hop`;
  - observability context now includes `__process_group`, `__handoff_from`, `__route_hop`;
  - trace sinks export these markers in JSONL/OTLP/OpenTracing payloads.
- [x] Added supervisor outbound route cache (February 14, 2026):
  - positive/negative cache for target-group resolution in boundary dispatch;
  - invalidation on process-group placement updates;
  - runtime knobs under `runtime.platform.routing_cache.*`.

Out of scope:

- full network endpoint implementation (FastAPI route registration);
- Redis backend rollout (tracked separately);
- production secret manager integration (Vault, KMS, etc.).

---

## Target architecture (5pre)

### 1) Process model

- parent process:
  - owns runtime bootstrap;
  - starts worker processes per `process_group`;
  - dispatches cross-group execution batches;
  - aggregates terminal outcomes and reply correlation.
- worker process:
  - bootstraps discovery + DI + runtime services from metadata bundle;
  - executes assigned workload loop for its process group;
  - returns structured boundary results.

### 2) Control plane and data plane

- control plane:
  - startup handshake (`BOOTSTRAP_BUNDLE`, `READY`, `HEARTBEAT`, `STOP`, `ACK`);
  - key/bootstrap exchange and lifecycle commands.
- data plane:
  - existing secure transport envelopes for workload and terminal outputs.

Both planes must use framework-native abstractions (services/adapters), not ad-hoc
runtime-only helpers.

### 3) Observability plane

- tracing source stays execution-observer based;
- add OTel exporter adapter (OTLP) as primary external trace pipeline;
- add OpenTracing bridge adapter for legacy systems;
- preserve existing JSONL/stdout adapters for deterministic local debugging.

### 4) Lifecycle and logging plane (engine-native)

- supervisor lifecycle is modeled as platform/system nodes:
  - `supervisor_start_groups`
  - `supervisor_wait_ready`
  - `supervisor_start_workload`
  - `supervisor_graceful_stop`
  - `supervisor_force_stop`
- worker lifecycle checkpoints emit structured log events:
  - `worker_spawned`, `worker_bootstrap_loaded`, `worker_ready`, `worker_running`,
    `worker_stopping`, `worker_stopped`, `worker_failed`.
- signal mapping:
  - process start (`main`) -> start scenario;
  - `SIGTERM` -> graceful stop path with timeout budget;
  - timeout overflow -> forced terminate path.
- all lifecycle transitions are emitted through platform observability adapters
  (console first, expandable to OTEL logs later).

### 5) Kubernetes integration semantics (readiness/termination)

- baseline readiness model is pull-based:
  - application exposes readiness endpoint/health source;
  - Kubernetes `readinessProbe` polls it;
  - app reports ready only after supervisor opens readiness gate.
- graceful termination model:
  - Kubernetes sends `SIGTERM` to pod process;
  - supervisor executes graceful stop scenario with timeout budget;
  - unfinished processes are force-stopped before pod termination.
- optional advanced push-style readiness:
  - via custom Pod readiness gates and Kubernetes API condition updates
    (requires dedicated controller/service-account/RBAC);
  - not required for phase baseline.

---

## Contracts to freeze

### Runtime config additions

`runtime.platform.process_groups[]` additions:

- `nodes: [str]` (already used);
- `workers: int` (default `1`);
- `runner_profile: str` (default `sync`);
- `heartbeat_seconds: int` (default `5`);
- `start_timeout_seconds: int` (default `30`);
- `stop_timeout_seconds: int` (default `30`).

`runtime.platform.execution_ipc.control`:

- `transport: tcp_local` (phase baseline);
- `bind_host: 127.0.0.1`;
- `bind_port: int` (0 allowed);
- `auth.mode: hmac`;
- `auth.ttl_seconds`;
- `auth.nonce_cache_size`;
- `max_payload_bytes`.

`runtime.observability.tracing` extension:

- `exporters: [ ... ]` where each exporter has:
  - `kind: jsonl | stdout | otel_otlp | opentracing_bridge`
  - `settings: mapping`.

`runtime.observability.logging` extension:

- `exporters: [ ... ]` where each exporter has:
  - `kind: stdout | jsonl | otel_logs_otlp`
  - `settings: mapping`
- `lifecycle_events.enabled: bool` (default `true`)
- `lifecycle_events.level: info | debug` (default `info`).

`runtime.platform.readiness` extension:

- `enabled: bool` (default `true`);
- `start_work_on_all_groups_ready: bool` (default `true`);
- `readiness_timeout_seconds: int` (default aligns with group start timeout).

### Envelope metadata requirements

- preserve `trace_id` end-to-end across parent -> worker -> parent;
- preserve `reply_to` when present;
- include transport metadata in context/internal fields only (no domain leakage).
- carry lifecycle/control correlation ids for startup/shutdown diagnostics.

---

## Recommended Jaeger path

Primary:

- framework trace observer
- `trace_otel_otlp` adapter
- OTel Collector
- Jaeger backend

Compatibility:

- `trace_opentracing_bridge` adapter for legacy tracer API integration.

Notes:

- Jaeger today is best integrated via OpenTelemetry protocol path;
- OpenTracing support is legacy/bridge mode, not primary design center.

---

## Detailed TDD sequence

### Step A — contract freeze and validator RED

Implement RED tests for:

- `P5PRE-CFG-01` unknown `process_groups[].workers` type rejected;
- `P5PRE-CFG-02` non-positive `workers` rejected;
- `P5PRE-CFG-03` invalid `control` IPC shape rejected;
- `P5PRE-CFG-04` invalid tracing exporter `kind` rejected;
- `P5PRE-CFG-05` `process_supervisor` mode requires control plane config;
- `P5PRE-CFG-06` invalid logging exporter kind rejected;
- `P5PRE-CFG-07` invalid readiness config rejected.

Deliverables:

- explicit schema tests in `tests/stream_kernel/config/*`;
- documented config examples in this plan and web architecture doc.

Status:

- [x] Completed on February 13, 2026.
- Added detailed contract freeze spec:
  - [web_phase5pre_stepa_contract_freeze_spec](web_phase5pre_stepa_contract_freeze_spec.md)
- Added validator coverage for Step A contract:
  - process-group runtime knobs (`workers`, lifecycle timeouts/profile);
  - `runtime.platform.readiness` contract;
  - `runtime.observability` tracing/logging exporter contracts;
  - optional `runtime.platform.execution_ipc.control` contract.
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py`
  - `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py tests/stream_kernel/app/test_framework_run.py`

### Step B — multiprocess supervisor contract RED

Add contract tests for real process semantics:

- `P5PRE-SUP-01` start groups spawns OS processes (pid is real, alive);
- `P5PRE-SUP-02` startup timeout yields deterministic runtime error;
- `P5PRE-SUP-03` graceful stop transitions to stopped state;
- `P5PRE-SUP-04` forced stop path emits deterministic diagnostics;
- `P5PRE-SUP-05` per-group workers count honored;
- `P5PRE-SUP-06` supervisor emits lifecycle console logs for each phase.

Deliverables:

- new supervisor contract tests in `tests/stream_kernel/platform/services/*`;
- no production code changes yet (RED expected).

Status:

- [x] Completed on February 13, 2026.
- Step B detailed RED spec added:
  - [web_phase5pre_stepb_multiprocess_supervisor_red_spec](web_phase5pre_stepb_multiprocess_supervisor_red_spec.md)
- RED tests added:
  - `tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`
- Implemented in Step D and now green:
  - `src/stream_kernel/platform/services/bootstrap.py` (`MultiprocessBootstrapSupervisor`)
  - `src/stream_kernel/platform/services/__init__.py` discovery export order (multiprocess before local)
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`
  - `.venv/bin/pytest -q tests/stream_kernel/application_context/test_service_decorator.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k process_supervisor`

### Step C — control channel implementation GREEN

Implement control channel and key/bootstrap delivery over framework rails:

- define control message models (`BootstrapBundle`, `Ready`, `StartWork`, `Stop`, `Heartbeat`);
- implement control transport adapter on top of secure tcp transport primitives;
- wire one-shot bootstrap bundle delivery per worker start.

TDD cases:

- `P5PRE-CTRL-01` bundle delivered once and acknowledged;
- `P5PRE-CTRL-02` duplicate bootstrap command rejected deterministically;
- `P5PRE-CTRL-03` invalid signature/ttl/replay on control channel rejected;
- `P5PRE-CTRL-04` workload start command is gated by READY handshake.

Status:

- [x] Completed on February 13, 2026.
- Step C detailed spec added:
  - [web_phase5pre_stepc_control_plane_tdd_spec](web_phase5pre_stepc_control_plane_tdd_spec.md)
- Added control-plane implementation:
  - `src/stream_kernel/execution/transport/control_plane.py`
- Added Step C test coverage:
  - `tests/stream_kernel/execution/transport/test_control_plane.py`
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/transport/test_control_plane.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/transport/test_secure_tcp_transport.py tests/stream_kernel/execution/transport/test_bootstrap_keys.py`

### Step D — real worker lifecycle GREEN

Implement `MultiprocessBootstrapSupervisor`:

- spawn workers;
- wait for ready acknowledgements;
- maintain lifecycle registry with pids and states;
- stop and force-terminate via control commands/timeouts;
- emit structured lifecycle logs to console sink.

TDD cases:

- `P5PRE-LIFE-01` deterministic start order by config;
- `P5PRE-LIFE-02` heartbeat timeout marks worker failed;
- `P5PRE-LIFE-03` crash detected and surfaced with deterministic category;
- `P5PRE-LIFE-04` SIGTERM triggers graceful stop flow before forced path.

Status:

- [x] Completed (baseline) on February 13, 2026.
- Detailed Step D spec:
  - [web_phase5pre_stepd_multiprocess_lifecycle_tdd_spec](web_phase5pre_stepd_multiprocess_lifecycle_tdd_spec.md)
- Implemented baseline contracts:
  - real spawn-based workers per process group;
  - `configure_process_groups(...)`, `snapshot()`, `lifecycle_events()`;
  - deterministic `wait_ready(timeout)` behavior;
  - graceful and forced stop lifecycle event emission.
  - dual stop strategy:
    - primary: `Event`-based graceful signaling;
    - fallback: terminate-only when stop-event primitives are unavailable.

### Step E — group execution delegation GREEN

Wire boundary execution to real workers:

- dispatch grouped boundary inputs to target worker groups;
- execute child loop in workers, return `RoutingResult` terminal outputs;
- preserve local fallback path only for `bootstrap.mode=inline`;
- block source/workload processing until supervisor readiness gate is open.

TDD cases:

- `P5PRE-EXEC-01` cross-group workload executed in target worker process;
- `P5PRE-EXEC-02` in-group workload remains local;
- `P5PRE-EXEC-03` terminal correlation preserved (`trace_id` aliasing where needed);
- `P5PRE-EXEC-04` no source ingestion before global readiness is reached.

Status:

- [x] Completed on February 13, 2026.
- Detailed Step E spec:
  - [web_phase5pre_stepe_boundary_delegation_tdd_spec](web_phase5pre_stepe_boundary_delegation_tdd_spec.md)
- Implemented:
  - `MultiprocessBootstrapSupervisor.execute_boundary(...)` with per-group worker selection;
  - per-worker control channels over `multiprocessing.Pipe`;
  - worker-side boundary execution via child bootstrap loop;
  - deterministic timeout/transport/execution error categories from worker responses.
- Added tests:
  - `tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py tests/stream_kernel/platform/services/test_bootstrap_supervisor_event_fallback.py tests/stream_kernel/execution/orchestration/test_remote_handoff_contract.py tests/stream_kernel/execution/orchestration/test_builder.py -k \"process_supervisor or handoff\"`

### Step F — observability adapters GREEN

Add framework adapters:

- `trace_otel_otlp`:
  - exports traces over OTLP gRPC/HTTP;
  - configurable endpoint, headers, batch options.
- `trace_opentracing_bridge`:
  - emits via OpenTracing-compatible bridge API;
  - maps framework span fields to legacy tags/logs.

TDD cases:

- `P5PRE-OBS-01` OTel exporter receives spans with trace continuity across processes;
- `P5PRE-OBS-02` OpenTracing bridge receives mapped spans deterministically;
- `P5PRE-OBS-03` exporter failure does not break business pipeline (observability isolation).

Status:

- [x] Completed on February 13, 2026.
- Detailed Step F spec:
  - [web_phase5pre_stepf_observability_adapters_tdd_spec](web_phase5pre_stepf_observability_adapters_tdd_spec.md)
- Implemented:
  - OTLP sink adapter: `trace_otel_otlp`;
  - OpenTracing bridge sink adapter: `trace_opentracing_bridge`;
  - tracing observer factory support for `runtime.observability.tracing.exporters[]`;
  - multi-exporter fan-out with exporter-failure isolation.
- Added tests:
  - `tests/adapters/test_trace_sinks.py` (OTLP/OpenTracing mapping + isolation);
  - `tests/stream_kernel/adapters/test_observability_adapters.py` (metadata/discovery coverage);
  - `tests/stream_kernel/observability/test_tracing_observer_factory.py` (factory path for observability exporters).
- Verified via:
  - `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py tests/stream_kernel/adapters/test_observability_adapters.py tests/stream_kernel/observability/test_tracing_observer_factory.py`
  - `.venv/bin/pytest -q tests/stream_kernel/observability/test_tracing_observer.py tests/stream_kernel/execution/orchestration/test_builder.py -k \"observability or tracing\" tests/stream_kernel/app/test_tracing_runtime.py`

### Step G — 4-process topology smoke scenario

Create deterministic smoke profile with 4 groups (fund_load baseline):

- `execution.ingress`: source + parse bridge;
- `execution.features`: time keys + idempotency + features;
- `execution.policy`: policy + windows;
- `execution.egress`: format + sink bridge.

TDD cases:

- `P5PRE-SMOKE-01` pipeline runs end-to-end in multiprocess profile;
- `P5PRE-SMOKE-02` output parity equals memory profile after `jq -cS` normalization;
- `P5PRE-SMOKE-03` trace continuity validated across all 4 groups;
- `P5PRE-SMOKE-04` lifecycle logs show deterministic start/ready/run/stop timeline.

Status:

- [x] Completed on February 13, 2026.
- Detailed Step G spec:
  - [web_phase5pre_stepg_smoke_topology_tdd_spec](web_phase5pre_stepg_smoke_topology_tdd_spec.md)
- Implemented:
  - iterative multi-hop boundary dispatch in multiprocess supervisor by `Envelope.target`;
  - target process-group resolution from `runtime.platform.process_groups[].nodes`;
  - lifecycle orchestration hook to pass process-group config into supervisor before startup.
- Added tests:
  - `tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py`
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py`
  - `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k "process_supervisor"`

### Step H — regression/parity gate

Run:

- existing Phase 2/3/4bis suites;
- new Phase 5pre supervisor/control/observability suites;
- baseline + experiment CLI parity;
- smoke topology parity.

Record dedicated report:

- `web_phase5pre_steph_regression_parity_report.md`.

Status:

- [x] Completed on February 13, 2026.
- Regression/parity report:
  - [web_phase5pre_steph_regression_parity_report](web_phase5pre_steph_regression_parity_report.md)
- Verified:
  - Phase 2/3/4bis compatibility suites are green;
  - new Phase 5pre supervisor/control/observability suites are green;
  - baseline/experiment CLI parity diffs are empty after `jq -cS` normalization;
  - Step G 4-process smoke topology remains green in consolidated run.

---

## Test matrix (summary)

- `P5PRE-CFG-*` config and validator contract.
- `P5PRE-SUP-*` supervisor lifecycle behavior.
- `P5PRE-CTRL-*` control channel security/semantics.
- `P5PRE-LIFE-*` runtime worker lifecycle and failures.
- `P5PRE-EXEC-*` delegation of grouped workloads.
- `P5PRE-OBS-*` observability exporters (OTel + OpenTracing bridge).
- `P5PRE-LOG-*` lifecycle logging and phase visibility.
- `P5PRE-READY-*` readiness-gated workload start behavior.
- `P5PRE-SMOKE-*` 4-process deterministic scenario.

---

## Risks and mitigations

Risk:

- control and data channel contract drift.
Mitigation:

- explicit message models + shared codec + strict validator.

Risk:

- observability exporter latency impacts pipeline.
Mitigation:

- async/batch exporter with bounded queue and drop policy diagnostics.

Risk:

- lifecycle logging flood in noisy environments.
Mitigation:

- bounded/sampled lifecycle event emission and explicit log level controls.

Risk:

- flaky process tests in CI sandbox.
Mitigation:

- split deterministic unit contracts from environment-dependent socket/process tests;
- mark environment-dependent tests with explicit skip reasons.

---

## Exit criteria

- real multiprocess supervisor is active in `process_supervisor` mode;
- per-group worker lifecycle is deterministic and observable;
- cross-group node execution is delegated to target worker process;
- OTel exporter path is working and Jaeger-compatible;
- OpenTracing bridge adapter exists for legacy stacks;
- lifecycle phases are visible in console/structured logs;
- readiness gate prevents early workload ingestion before worker readiness;
- baseline/experiment outputs preserve deterministic parity.
