# Web Phase 5pre: Multiprocess Handoff + OTLP HTTP + Jaeger E2E (TDD)

## Goal

Close the remaining operational gap before web endpoint work:

1. stabilize multiprocess boundary handoff for real project topology (`fund_load` multiprocess configs);
2. implement real OTLP HTTP trace export (not only in-memory span mapping);
3. provide deterministic end-to-end runbook for Jaeger visualization including cross-process route hops.

---

## Scope

In scope:

- boundary execution error diagnostics propagation (`child -> supervisor -> runtime`);
- multiprocess handoff fix validated on `baseline_config_newgen_multiprocess.yml`;
- OTLP HTTP exporter in framework trace sink (`trace_otel_otlp`);
- docs + runnable commands for local Jaeger stack.

Out of scope:

- data-bus backend switch (ZeroMQ / Redis streams);
- FastAPI endpoint orchestration;
- Kubernetes deployment wiring.

---

## TDD Steps

## Step A — Handoff failure diagnostics (RED -> GREEN)

### RED tests

- `P5PRE-HANDOFF-01`: when child node fails in boundary loop, supervisor error must include child failure details.
- `P5PRE-HANDOFF-02`: runtime worker failure should preserve target group and child message context.

### GREEN target

- supervisor returns deterministic failure category and detailed message payload;
- runtime exception text no longer hides child failure root cause.

## Step B — Multiprocess handoff fix on real topology (RED -> GREEN)

### RED tests

- `P5PRE-HANDOFF-03`: `fund_load` multiprocess baseline config completes successfully with CLI `--input/--output` overrides.
- `P5PRE-HANDOFF-04`: output cardinality/order parity remains equal to single-process baseline for same input sample.

### GREEN target

- no `remote handoff failed` in multiprocess baseline run;
- deterministic output and route metadata remain intact.

## Step C — Real OTLP HTTP export (RED -> GREEN)

### RED tests

- `P5PRE-OTLP-01`: `trace_otel_otlp` performs HTTP POST to configured endpoint with OTLP-compatible JSON payload.
- `P5PRE-OTLP-02`: request headers from config are propagated.
- `P5PRE-OTLP-03`: exporter failures increment dropped counter without breaking execution.

### GREEN target

- default implementation exports over HTTP (`urllib` stdlib path);
- optional injected transport remains for tests.

## Step D — Jaeger E2E docs and runnable recipe

### Deliverables

- docker-compose + collector config example;
- config snippet for `runtime.observability.tracing.exporters` with `otel_otlp`;
- exact run commands for:
  - single-process trace verification;
  - multiprocess trace verification;
  - viewing process route hops (`process_group`, `handoff_from`, `route_hop`) in Jaeger.

---

## Exit Criteria

- multiprocess baseline run succeeds locally with real input file;
- traces are exported over HTTP to collector and visible in Jaeger;
- docs contain copy-paste commands and expected observable outputs.

---

## Status (current)

- [x] Step A complete:
  - child failure details are propagated through supervisor/runtime error chain.
- [x] Step B complete:
  - child bootstrap now receives runtime config and applies `config.nodes.*` in worker process;
  - worker process reuses bootstrapped child runtime between boundary calls (source state no longer resets each call).
- [x] Step C complete:
  - `trace_otel_otlp` performs real HTTP POST to OTLP endpoint with deterministic OTLP-like JSON payload;
  - custom headers and timeout are supported;
  - exporter failures are isolated (`dropped` counter).
- [x] Step D complete:
  - Jaeger local runbook added:
    - `docs/framework/initial_stage/web/analysis/Jaeger OTLP multiprocess runbook.md`
  - dedicated config added:
    - `src/fund_load/baseline_config_newgen_multiprocess_jaeger.yml`
