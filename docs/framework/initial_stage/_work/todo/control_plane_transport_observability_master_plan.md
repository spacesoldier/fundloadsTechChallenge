# Control-plane, IPC transport, and observability roadmap

## Purpose

Provide a single, ordered roadmap that stitches together the existing TDD
plans for control-plane refactor, IPC transport, and observability isolation.

## Inputs

- `ipc_transport_ports_and_adapters_tdd_plan.md`
- `execution_ipc_tcp_bytes_transport_tdd_plan.md`
- `runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md`
- `supervisor_control_plane_architecture_transition_tdd_plan.md`
- `observability_dedicated_service_process_tdd_plan.md`
- `observability_backpressure_and_prometheus_exporter_tdd_plan.md`

## Principles

- Docs first, tests second, code last.
- IPC ports stay transport-only; buffering is port policy.
- Control-plane commands are unbuffered and prioritized.

## Status (2026-02-22)

1. Phase 0 — in progress (buffering + `AdapterBatch` contract tests added).
2. Phase 1 — in progress (IPC buffering service + tests; DI bindings + buffer config validation added).
3. Phase 2 — pending.
4. Phase 3 — pending.
5. Phase 4 — pending.
6. Phase 5 — pending.

## Phase 0 — Contracts and vocabulary

Goals:
- lock `ExecutionIpcPort` contract and buffering policy terms;
- define `AdapterBatch[T]` behavior and batch expansion rules;
- define metrics for buffer depth and latency.

Source plans:
- `ipc_transport_ports_and_adapters_tdd_plan.md` Phase A
- `runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md` Phase A

Exit criteria:
- contract tests exist for port buffering and batch expansion;
- docs describe batch wrapper and ACK timing.

## Phase 1 — IPC buffering in transport service

Goals:
- implement receive-side buffering in IPC transport service;
- register buffered receivers in platform KV store;
- expose buffer metrics.

Source plans:
- `ipc_transport_ports_and_adapters_tdd_plan.md` Phases B–G

Exit criteria:
- tests pass for buffered receive and batch flush;
- control-plane messages bypass buffers.

## Phase 2 — Bytes IPC transport

Goals:
- replace object `send/recv` with bytes framing for IPC;
- keep port API stable while swapping adapter.

Source plans:
- `execution_ipc_tcp_bytes_transport_tdd_plan.md` Steps A–F

Exit criteria:
- bytes-only IPC path in process supervisor mode;
- ACK semantics preserved.

## Phase 3 — Control-plane system nodes

Goals:
- move supervisor logic into control-plane system nodes;
- lifecycle and transport become injectable services.

Source plans:
- `supervisor_control_plane_architecture_transition_tdd_plan.md`

Exit criteria:
- no direct `Pipe` usage in supervisor logic;
- control-plane nodes are discoverable and test-covered.

## Phase 4 — Observability isolation

Goals:
- observability runs in a dedicated process group;
- tracing/logging/monitoring use async dispatch and buffering;
- Prometheus export pipeline stable.

Source plans:
- `observability_dedicated_service_process_tdd_plan.md`
- `observability_backpressure_and_prometheus_exporter_tdd_plan.md`

Exit criteria:
- observability pipeline does not block business hot path;
- metrics visible via Prometheus exporter.

## Phase 5 — Regression and performance gates

Goals:
- ensure parity with baseline output ordering;
- add perf gates to prevent regressions.

Source plans:
- `platform_api_phaseG_regression_perf_gate_tdd_plan.md`

Exit criteria:
- perf gates green;
- multiprocess output parity preserved.

## Out of scope (for this roadmap)

- FastAPI integration
- Redis-backed queues/topics
- Multi-host transport
