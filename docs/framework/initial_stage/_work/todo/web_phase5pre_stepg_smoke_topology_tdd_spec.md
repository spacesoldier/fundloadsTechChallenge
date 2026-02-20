# Web Phase 5pre Step G: 4-process topology smoke scenario (TDD spec)

## Goal

Validate that process-supervisor execution can run a deterministic multi-hop
pipeline across four process groups and return a terminal reply to caller
correlation without local fallback.

## Scope

In scope:

- 4-group process placement via `runtime.platform.process_groups[].nodes`;
- parent-supervisor iterative boundary dispatch (multi-hop by `Envelope.target`);
- deterministic terminal reply completion through `ReplyCoordinatorService`;
- lifecycle events for worker spawn/ready/stop timeline.

Out of scope:

- full fund_load parity `jq+diff` profile;
- external tracing backend assertions (Jaeger/OTLP integration tests);
- benchmark/perf characterization for multi-hop throughput.

## Runtime contract (Step G)

Required runtime paths:

- `runtime.platform.bootstrap.mode = process_supervisor`;
- `runtime.platform.process_groups` with explicit `name`, `workers`, `nodes`;
- boundary transport profile already enabled by previous steps
  (`runtime.platform.execution_ipc.transport = tcp_local`).

Execution contract:

- initial boundary input is routed by target placement map;
- each worker executes one targeted node and returns envelopes;
- supervisor re-dispatches targeted envelopes to their target process group;
- envelopes without target are treated as terminal outputs.

## TDD cases

- `P5PRE-SMOKE-01` 4-group synthetic topology completes end-to-end reply.
- `P5PRE-SMOKE-02` multi-hop dispatch uses process-group node placement.
- `P5PRE-SMOKE-03` trace correlation survives all hops (`trace_id` reaches waiter completion).
- `P5PRE-SMOKE-04` lifecycle events include deterministic spawn/ready/stop phases.

## Files expected

- `tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py`
  - Step G smoke contract.
- `src/stream_kernel/platform/services/bootstrap.py`
  - iterative execute-boundary routing and target-group resolution.
- `src/stream_kernel/execution/orchestration/lifecycle_orchestration.py`
  - process-group configuration handoff to supervisor before startup.

## Validation commands

- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k "process_supervisor"`
