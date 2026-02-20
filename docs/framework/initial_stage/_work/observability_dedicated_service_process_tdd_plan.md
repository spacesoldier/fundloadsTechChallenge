# Observability dedicated service process on platform rails (TDD plan)

## Goal

Move tracing, logging, and monitoring handling out of the supervisor hot path into a dedicated service process while preserving existing platform rails:

- workers: business work only;
- supervisor: routing/control only;
- observability process: system nodes + platform services + adapters/exporters.

Target outcomes:

- lower supervisor overhead under load;
- deterministic graceful stop with explicit observability drain accounting;
- config-driven routing/delegation for system traffic, same style as business process groups;
- no hardcoded side channels.

---

## Why this is needed

Current `service_worker` thread model still keeps observability ownership in supervisor process.  
That limits isolation and creates contention points (routing + observability lifecycle in one process).

We need process-level isolation, not just loop/thread-level isolation.

---

## Scope

In scope:

- dedicated multiprocess observability group (`system.observability`);
- system nodes for tracing/logging/monitoring dispatch executed in that group;
- platform service wiring for observability pipeline in that group;
- supervisor reduced to transport/router + lifecycle coordination;
- auto-default config in multiprocess mode with explicit override support;
- TDD for routing, startup, shutdown, loss accounting, and perf guardrails.

Out of scope:

- distributed broker migration (Redis/Kafka);
- cross-host observability workers;
- redesign of business DAG semantics.

---

## Architecture target

### Process roles

- worker groups (`execution.*`): business nodes only.
- supervisor process:
  - accepts/forwards envelopes;
  - maintains route tables and worker control;
  - does not own observability exporters in target model.
- observability process group (`system.observability`):
  - executes `system.obs.*` nodes;
  - owns observability platform services;
  - owns tracing/logging/monitoring adapters/exporters.

### Messaging model

- business workers emit observability envelopes through existing platform routing.
- supervisor routes `system.obs.*` targets to observability group by process-group router.
- recursion guard stays mandatory:
  - observability service traffic must not recursively generate new observability traffic from the same pipeline.

### Ownership model

- one owner process for exporters/endpoints:
  - OTLP emitters;
  - log file/stdout sinks;
  - Prometheus HTTP/textfile sink.

This removes duplicate endpoint binding and keeps exporter queues centralized.

---

## Config contract (planned)

### 1) Auto-default in multiprocess mode

When all are true:

- `runtime.platform.bootstrap.mode=process_supervisor`
- observability is enabled (any tracing/logging/monitoring exporter enabled)
- no explicit system observability group declared

runtime materializes default group:

- `name: system.observability`
- `workers: 1`
- `runner_profile: async`
- nodes: all enabled `system.obs.*` dispatch nodes

### 2) Explicit override

```yaml
runtime:
  platform:
    process_groups:
      - name: system.observability
        workers: 1
        runner_profile: async
        nodes:
          - system.obs.trace_dispatch
          - system.obs.log_dispatch
          - system.obs.metrics_dispatch
```

### 3) Strict validation constraints

- system group names must be unique and resolvable by router;
- `system.obs.*` nodes cannot be assigned to multiple groups simultaneously;
- if observability exporters are enabled in multiprocess mode, exactly one effective owner group must exist.

---

## TDD phases

## Phase A — Contract freeze + validator RED

Status (2026-02-19): completed.

### RED

Add failing tests for:

- default system observability group materialization rules;
- duplicate/ambiguous ownership of `system.obs.*` nodes;
- strict rejection when enabled observability has no resolvable owner group.

### GREEN

Implement validator/normalizer support for:

- effective observability owner group resolution;
- deterministic defaults in `process_supervisor` mode.

### REFACTOR

- keep compatibility with existing config files;
- preserve explicit override precedence.

Exit criteria:

- contract is stable and test-covered;
- config errors are explicit and actionable.

---

## Phase B — Router and bootstrap wiring

Status (2026-02-19): completed.

### RED

Add tests:

- `system.obs.*` envelopes route to `system.observability` group;
- supervisor never tries to execute `system.obs.*` locally in multiprocess target mode;
- startup readiness waits for observability group similarly to business groups.

### GREEN

Implement:

- process-group router mapping for system nodes;
- bootstrap creation of observability worker process;
- lifecycle events marking observability group ready/stopped.

### REFACTOR

- unify route resolution code path (business + system) to avoid hardcoded branches.

Exit criteria:

- observability route is platform-routed, not special-cased inline.

---

## Phase C — System nodes/services/adapters ownership transfer

Status (2026-02-19): completed.

### RED

Add tests:

- exporters are instantiated only in observability process group;
- business workers do not initialize observability exporters;
- supervisor does not bind monitoring HTTP endpoint.

### GREEN

Implement:

- observability service/bootstrap ownership transfer to `system.observability`;
- node/service registry wiring for tracing/logging/monitoring dispatch in that group.

### REFACTOR

- remove obsolete supervisor-local service-worker code paths (or gate as temporary fallback flag).

Exit criteria:

- single-process ownership for observability adapters is enforced.

---

## Phase D — Graceful stop and drain guarantees

Status (2026-02-19): completed.

### RED

Add tests:

- stop sequence drains pending observability envelopes before exporter close;
- forced stop exposes deterministic loss counters (`pending`, `dropped`, `timed_out`);
- no deadlock between supervisor stop and observability worker close.

### GREEN

Implement:

- ordered shutdown protocol:
  1. stop business ingress;
  2. drain inter-group observability traffic;
  3. close exporters in observability process;
  4. stop remaining workers.

### REFACTOR

- centralize shutdown diagnostics in lifecycle/all logs for grep-friendly auditing.

Exit criteria:

- graceful stop keeps no silent observability loss.

---

## Phase E — Performance and pressure behavior

Status (2026-02-19): completed.

### RED

Add perf/integration checks:

- supervisor processing latency under load improves vs baseline;
- no regression in output determinism/order;
- queue pressure metrics captured in monitoring exporter.

### GREEN

Tune defaults:

- queue capacities/backpressure for observability process;
- small-batch flush defaults for near-realtime OTLP.

### REFACTOR

- document knobs and safe ranges in runbook.

Exit criteria:

- process isolation brings measurable overhead reduction in supervisor path.

---

## Phase F — Rollout and migration closure

Status (2026-02-19): completed.

### RED

Regression suite:

- baseline + experiment multiprocess configs;
- Jaeger + file traces + monitoring endpoint;
- lifecycle log consistency.

### GREEN

Finalize:

- migration notes from `service_worker` thread model to dedicated process model;
- default/override examples in docs.

### REFACTOR

- remove deprecated transitional config paths after parity confirmation.

Exit criteria:

- dedicated observability process becomes default platform path for multiprocess mode.

---

## Phase E.1 — Post-rollout latency and diagnostics hardening

Status (2026-02-19): completed.

### RED

Add focused regression checks:

- `wait_ready()` must wait for `worker_bootstrapped` when child runtime bundle is active;
- supervisor must support fire-and-forget dispatch for `system.obs.*` workloads routed to dedicated observability owner group;
- trace-dispatch dropped counters must avoid double counting between loop-level and submit-level fields;
- dispatch wait timing must be exported to monitoring metrics (Grafana-visible).

### GREEN

Implement:

- bootstrap readiness barrier on worker bootstrap signal;
- non-blocking no-reply dispatch path for observability owner group;
- corrected dropped/loss aggregation formula;
- `dispatch_wait_*` diagnostics propagated to lifecycle snapshot + monitoring metrics exporters.

### REFACTOR

- keep lifecycle event payloads grep-friendly while adding wait timing fields.

Exit criteria:

- startup diagnostics are truthful, observability routing latency is measurable, and Grafana can plot supervisor dispatch wait.

---

## Risks and mitigations

- Extra IPC hop cost:
  - mitigate with bounded queues + backpressure policy + micro-batch flush tuning.
- Misconfigured ownership:
  - strict validator rules + startup fail-fast.
- Recursive observability storms:
  - enforce `system.obs.*` recursion guard in routing/dispatch path.

---

## Acceptance checklist

- `system.observability` process appears in lifecycle logs and readiness flow.
- Supervisor logs show routing/control only (no exporter ownership bind events).
- Worker groups do not bind observability exporters/endpoints.
- Graceful stop reports pending/dropped/exported counters with deterministic totals.
- Output determinism and ordering remain unchanged.
