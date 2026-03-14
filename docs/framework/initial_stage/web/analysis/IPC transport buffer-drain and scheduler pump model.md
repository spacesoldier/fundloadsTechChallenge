# IPC transport buffer-drain and scheduler pump model

## Problem statement

Current root/leaf polling path can call:

- `system.scheduler.tick` -> source node
- source node -> `poll_next_*`
- `poll_next_*` -> `ipc.recv(...)`
- `ipc.recv(...)` (pipe adapter fast-path) -> direct `endpoint.recv(timeout=0.0)`

This reintroduces direct channel reads from orchestration-level polling code.
When load grows, timer cadence and direct lane polling can interfere with transport fairness and flow-control behavior.

## Target ownership model

Transport must own channel I/O end-to-end:

1. `pipe/socket -> transport receive buffer`
   - background reader loop only (adapter-owned);
   - includes ACK/control signal handling.
2. `transport receive buffer -> runner input queue`
   - scheduler-driven pump;
   - no direct `endpoint.recv` outside adapter reader loop.

Orchestration polling code must read buffered messages only.

## Scheduler role (strictly limited)

`system.scheduler.tick` is not a business orchestrator.
It is an I/O pump trigger only:

- dispatch due transport-ingress jobs;
- pull from transport buffers;
- emit envelopes/events to runner queue.

Business execution remains event-driven after message ingress into runner queue.

## Drain policy

Use weighted round-robin with starvation guard:

- mandatory minimum service for `control` lane on each tick;
- higher weight for `data`;
- lower but non-zero weights for `trace/log/metric`.

Reference default profile (can be made configurable later):

- lane weights: `control=4, data=8, trace=2, log=1, metric=1`
- per-target burst cap: `16`
- global per-tick budget: `256`

Deterministic order requirements:

- stable lane order;
- stable target order within lane;
- no randomization in drain arbitration.

## ACK and flow-control

ACK handling remains transport-owned:

- ACK read/parse inside adapter reader loop;
- credit release inside transport coordinator callback;
- no ACK parsing in scheduler pump nodes.

Scheduler pump only consumes already decoded user payloads from receive buffers.

## Required code changes

1. Add buffer-only receive API in IPC transport stack:
   - adapter-level `recv_buffered(...)`;
   - coordinator-level delegation `recv_buffered(...)`.
2. Update root/leaf ingress services to prefer buffer-only receive.
3. Keep existing `recv(...)` for compatibility paths that explicitly need direct drain behavior.
4. Add scheduler-pump fairness policy tests and only then implement weighted draining.

## TDD plan and test cases

### Phase A: buffer-only receive contract

- `IPC-BUF-001` pipe adapter exposes `recv_buffered(target_id, timeout)` and returns user payload when reader loop has drained it.
- `IPC-BUF-002` coordinator exposes `recv_buffered(...)` and delegates to adapter buffered path when available.
- `IPC-BUF-003` leaf command ingress uses buffered receive path (does not require direct `recv`).
- `IPC-BUF-004` root leaf-ingress service uses buffered receive path (does not require direct `recv`).

Acceptance:

- tests pass without changing business payload semantics;
- ACK/control signals continue to be filtered from user path.

### Phase B: scheduler pump fairness

- `IPC-PUMP-001` control lane is serviced every tick under sustained data load.
- `IPC-PUMP-002` data lane receives larger throughput share than observability lanes.
- `IPC-PUMP-003` trace/log/metric lanes are not starved.
- `IPC-PUMP-004` deterministic replay of same buffered snapshot yields same drain order.

Acceptance:

- fairness tests pass for synthetic multi-lane load;
- no payload loss in pump path.

### Phase C: isolation and regression E2E

- `IPC-E2E-001` ingress->transform->egress chain preserves input cardinality and order.
- `IPC-E2E-002` same chain + observability lane preserves data outputs and drains observability without deadlock.
- `IPC-E2E-003` flow-control window saturation does not block send caller path.

Acceptance:

- all E2E tests green in async profile;
- no deadlocks/timeouts in nominal CI budget.

## Answer to architecture question

Single scheduled call does not have to "run the whole system".
It should only trigger transport buffer-drain work.
Business execution remains message-driven in runner after ingress.
