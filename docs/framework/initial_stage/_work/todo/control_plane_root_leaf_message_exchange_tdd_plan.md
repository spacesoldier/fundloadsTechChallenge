# Control-plane root-leaf message exchange protocol (TDD plan)

## Goal

Move root/leaf startup from heavy spawn-time bundle transfer to explicit message-driven handshake over control IPC lane.

Target sequence:

1. `leaf_hello`
2. `leaf_discovery_request`
3. `leaf_discovery_ack`
4. `leaf_config_card`
5. `leaf_config_ack`
6. `ready`

Protocol spec:

- [Control-plane root-leaf message exchange protocol](../../web/analysis/Control-plane%20root-leaf%20message%20exchange%20protocol.md)

Related plans:

- [Platform startup full-rails gap closure](platform_startup_full_rails_gap_closure_tdd_plan.md)
- [Leaf full platform-rails + async runtime](leaf_full_platform_rails_async_runtime_tdd_plan.md)

## Definition of done

1. Root startup for each worker uses typed discovery+config handshake messages.
2. Leaf does not parse external config source directly in startup path.
3. Startup failures are explicit (`rejected` or timeout) and observable.
4. Legacy mode remains available behind compatibility switch during rollout.
5. End-to-end multiprocess startup completes without repeated spawn loops and without long readiness stalls.

## Test matrix (high-level)

1. Contract tests
- event validation and serialization for all new handshake messages.

2. Node/service tests
- root handshake nodes/services emit expected next-stage messages.
- leaf handshake nodes/services produce deterministic acks.

3. Orchestration tests
- root readiness barrier opens only after all workers are `config_applied`.
- rejection/timeout transitions to failed startup state.

4. E2E tests
- nominal startup path with real IPC spawn.
- rejection path (missing node metadata).
- timeout path (no discovery/config ack).

5. Observability tests
- handshake stage duration metrics emitted.
- lifecycle logs emitted per stage.

## Phase A — protocol contracts and wire format

RED tests:

1. New runtime events exist with strict validation:
- `ControlPlaneLeafDiscoveryRequestEvent`
- `ControlPlaneLeafDiscoveryAckEvent`

2. IPC bytes codec roundtrip supports both events.

GREEN code:

1. Add typed events to `control_plane_events.py`.
2. Extend IPC codec encode/decode for new events.

## Phase B — root-side handshake orchestration

RED tests:

1. On `LeafHello`, root emits `LeafDiscoveryRequest` (protocol `v2`).
2. On `LeafDiscoveryAck(accepted)`, root emits `LeafConfigCard`.
3. On `LeafDiscoveryAck(rejected)`, root writes failed state and does not emit config.

GREEN code:

1. Add root handshake stage service with per-worker state.
2. Integrate into root reply ingress dispatch path.

## Phase C — leaf-side discovery stage

RED tests:

1. Leaf discovery request triggers local discovery subset check and emits `accepted` ack.
2. Missing required nodes emits `rejected` ack with deterministic error.

GREEN code:

1. Add leaf node/service stage for discovery request handling.
2. Use platform discovery service/store; no direct global config parse.

## Phase D — readiness and timeout semantics

RED tests:

1. Root waits for discovery and config acks with explicit stage timeouts.
2. Timeout yields typed failure state and startup abort.
3. No repeated spawn-request loops in a single root pulse cycle.

GREEN code:

1. Extend root loop readiness gates with stage-aware checks.
2. Add idempotent request tracking via `request_id`.

## Phase E — observability and latency diagnostics

RED tests:

1. Emit stage metrics:
- `control_plane.handshake.stage_duration_ms`
- `control_plane.handshake.total_duration_ms`

2. Emit lifecycle logs per stage.

GREEN code:

1. Add handshake metric/log emission via platform observability nodes/services.
2. Keep startup path non-blocking; no additional busy waits.

## Immediate implementation slice (this iteration)

1. Complete Phase A (contracts + codec + tests). DONE
2. Prepare Phase B test scaffolding for root orchestration service. DONE
3. Implement Phase C leaf discovery node + planning wiring. DONE
4. Align runtime-path reply pumping for startup handshake (barrier-open fast path). IN PROGRESS

## Current status (2026-03-01)

1. Phase A: DONE
- Events + validation + IPC codec roundtrip tests are green.

2. Phase B: DONE (compatibility mode retained)
- Root reply ingress supports v1 and v2 startup handshake modes.

3. Phase C: DONE
- Leaf discovery request node implemented.
- Worker control-plane planning includes discovery stage and consumer mapping.

4. Phase D: IN PROGRESS
- Startup reply pump fixed for the barrier-already-open path.
- Remaining gap: full real-runtime handshake progression in experimental multiprocess config still lacks explicit `group_startup_ready` stage logs.

5. Phase E: NOT STARTED
