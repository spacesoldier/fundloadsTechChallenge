# Leaf Full Platform-Rails + Async Runtime Gap Closure (TDD plan)

## Goal

Close remaining leaf-process gaps caused by sync polling and mixed orchestration ownership.

Target state:

- leaf startup is fully message-driven (`@node -> @service -> @adapter`);
- leaf control ingress is async/non-blocking in hot path;
- leaf worker uses async runner by default;
- no inline process-entry handshake loop remains.

Related docs:

- [Control-plane bootstrap root-leaf sequences](../../web/analysis/Control-plane%20bootstrap%20root-leaf%20sequences.md)
- [Platform startup full-rails gap closure (root)](platform_startup_full_rails_gap_closure_tdd_plan.md)
- [Control-plane, IPC transport, and observability roadmap](control_plane_transport_observability_master_plan.md)

## Evidence Snapshot (current code)

1. Inline leaf orchestration still lives in process entry:
   - `src/stream_kernel/execution/orchestration/lifecycle/leaf/command/control_plane_service.py:29`
   - `leaf_worker_process_entry` performs bootstrap, sends `leaf_hello`, then runs control-loop handshake inline.
2. Control ingress is synchronous polling:
   - `src/stream_kernel/execution/orchestration/lifecycle/leaf/command/command_loop_service.py:75`
   - `_recv_control_message` uses `poll(...)/recv()` directly (`command_loop_service.py:172`).
3. Root spawn defaults to non-explicit profile (`auto`):
   - `src/stream_kernel/execution/orchestration/lifecycle/root/startup/lifecycle_service.py:79`
4. Leaf effective profile can become sync:
   - `src/stream_kernel/execution/orchestration/lifecycle/leaf/startup/runtime_bootstrap_service.py:158`
   - computes `"async"` only when async pools detected; otherwise `"sync"`.
5. Boundary execution may run sync runner:
   - `src/stream_kernel/execution/orchestration/lifecycle/leaf/runtime/boundary_runtime.py:171`
   - runner mode selected from effective profile / node async detection.

## Gap Map

### LG-01: process-entry orchestration bypasses runner startup rails

Leaf process starts with imperative handshake instead of queue-driven init pulse and system nodes.

### LG-02: sync control polling in leaf can block coordination under load

Polling and message processing are tied to sync control-loop cadence.

### LG-03: startup handshake ordering is not fully represented as node events

`leaf_hello -> config_card -> ack` is partially executed outside runner message flow.

### LG-04: async-by-default policy is not guaranteed

Profile defaults and bootstrap inference allow implicit sync execution.

### LG-05: leaf observability finalization is procedural in command loop

Session finalization uses direct service callback from loop service, not explicit lifecycle events.

## Definition Of Done

1. Leaf process entry only:
   - builds minimal runtime shell;
   - enqueues `ControlPlaneLeafPulse`;
   - starts async runner loop.
2. `leaf_hello`, `leaf_config_card` apply, `leaf_config_ack`, boundary commands, and stop ack are node-driven.
3. No sync polling loop in process entry path.
4. Default leaf runner profile is `async`; sync is explicit opt-in only.
5. Leaf lifecycle/observability completion is event-driven through nodes/services.
6. Control-plane e2e passes without busy-wait loops or unbounded blocking.

## Delivery Plan (TDD)

### Phase A — Freeze leaf startup contracts

RED tests:

1. Leaf entry enqueues `ControlPlaneLeafPulse` and does not run inline handshake loop.
2. Leaf pulse node produces deterministic startup event chain.
3. Startup ordering is deterministic for:
   - `leaf_pulse`
   - `leaf_hello`
   - `leaf_config_card`
   - `leaf_config_ack`

GREEN code:

- Introduce/confirm typed startup event contracts for leaf pulse path.
- Route startup trigger into leaf system nodes.

### Phase B — Replace sync polling with async ingress rails

RED tests:

1. Leaf control ingress drains IPC control channel asynchronously and emits typed command events.
2. No direct `poll/recv` loop in process entry.
3. Stop path remains graceful with bounded timeout.

GREEN code:

- Add leaf ingress node/service pair for async control command intake.
- Keep adapter access behind existing transport ports.

### Phase C — Node-driven handshake and command handling

RED tests:

1. `leaf_config_card -> activation -> leaf_config_ack` is handled by nodes/services.
2. Boundary execute command and stop command are handled by nodes/services.
3. Lifecycle state updates use platform state services only.

GREEN code:

- Move command dispatch out of process entry.
- Keep leaf command loop logic only as node-invoked service behavior.

### Phase D — Enforce async-by-default policy

RED tests:

1. Omitted `runner_profile` resolves to `async` for leaf workers.
2. Explicit `runner_profile=sync` remains supported.
3. Boundary runtime chooses async runner by default profile.

GREEN code:

- Change root lifecycle default profile from `auto` to `async`.
- Keep sync path guarded by explicit config.

### Phase E — Remove remaining leaf hardcode and finalize events

RED tests:

1. Process entry contains no handshake/control orchestration logic.
2. Observability finalization happens via typed lifecycle events.
3. Leaf shutdown produces deterministic stop-ack and finalize events.

GREEN code:

- Delete legacy inline helpers from entrypoint path.
- Convert finalize callbacks to event-driven node/service path.

### Phase F — Stability and performance gates

RED tests:

1. No blocking sleeps in leaf hot control path.
2. Repeated multiprocess startup/handshake e2e runs stay stable.
3. Latency regression guard for root<->leaf control-plane roundtrip.

GREEN code:

- Add non-blocking and latency guard assertions to leaf control-plane e2e suites.

## Regression Gates

1. `tests/stream_kernel/execution/orchestration/lifecycle/leaf/**`
2. `tests/stream_kernel/execution/orchestration/control_plane/e2e/test_control_plane_real_spawn_ipc_*`
3. `tests/stream_kernel/execution/orchestration/runtime/test_startup_barrier_runtime_e2e.py`
4. `tests/stream_kernel/execution/runtime/test_async_runner.py`

## Execution Status

- [x] Phase A
- [x] Phase B
- [x] Phase C
- [x] Phase D
- [x] Phase E
- [x] Phase F

## Notes

- Strict order per phase: docs -> tests -> code.
- No compatibility shims for removed inline leaf bootstrap/hardcode paths.
- Replace legacy-entry tests with node/service contract tests under matching leaf module structure.
