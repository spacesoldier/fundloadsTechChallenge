# Control-plane root-leaf message exchange protocol

## Scope

This document defines the target startup protocol between root (supervisor) and leaf (worker) processes for `process_supervisor` mode.

Key objective:

- remove heavy spawn-time state transfer as a single pickled runtime bundle,
- move startup into explicit message exchange over IPC control lane,
- keep a single source of truth for runtime configuration in root,
- make startup observable, retryable, and versioned.

Related docs:

- [Control-plane bootstrap root-leaf sequences](Control-plane%20bootstrap%20root-leaf%20sequences.md)
- [Root async bootstrap with discovery and config streams](Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)
- [IPC transport and worker lifecycle services](IPC%20transport%20and%20worker%20lifecycle%20services.md)
- [Platform startup full-rails gap closure (TDD plan)](../../_work/todo/platform_startup_full_rails_gap_closure_tdd_plan.md)

## Design constraints

1. Root is the only authority that reads/parses external config source (YAML now, DB later).
2. Leaf must not re-read global config source on startup path.
3. Startup protocol is versioned and backward-compatible by explicit `protocol_revision`.
4. Handshake is deterministic and bounded by explicit timeouts.
5. Root startup does not busy-wait with unbounded blocking loops.

## Startup lanes

Three logical lanes are required end-to-end:

1. `control` lane
- handshake and lifecycle commands (`hello`, discovery/config requests, `stop`, acks).
- priority: high.

2. `data` lane
- business payload exchange.
- priority: medium.

3. `observability` lane
- logs/traces/metrics/monitoring dispatch.
- priority: low, batched, drop/backpressure policy configurable.

This protocol document focuses on the `control` lane.

## Protocol actors

1. `RootHandshakeOrchestrator` (`@node -> @service` chain in root)
- manages state machine per `worker_id`.

2. `LeafHandshakeResponder` (`@node -> @service` chain in leaf)
- answers root requests and reports readiness.

3. `Control transport service` (`@service`) with IPC adapters (`@adapter`)
- send/recv typed control-plane events.

## Message model (typed events)

### Existing messages used

1. `ControlPlaneLeafHelloEvent`
2. `ControlPlaneLeafConfigCardEvent`
3. `ControlPlaneLeafConfigAckEvent`
4. `ControlPlaneLeafStopCommand`
5. `ControlPlaneLeafStopAckEvent`

### New handshake messages (protocol extension)

1. `ControlPlaneLeafDiscoveryRequestEvent` (root -> leaf)
- fields:
  - `target_group`
  - `worker_id`
  - `request_id`
  - `required_nodes: tuple[str, ...]`
  - `include_relationships: bool`
  - `protocol_revision: int`

2. `ControlPlaneLeafDiscoveryAckEvent` (leaf -> root)
- fields:
  - `target_group`
  - `worker_id`
  - `request_id`
  - `status: accepted|rejected`
  - `discovered_nodes: tuple[str, ...]`
  - `missing_nodes: tuple[str, ...]`
  - `error: str | None`

## Root startup state machine (per worker)

States:

1. `spawned`
2. `hello_received`
3. `discovery_requested`
4. `discovery_accepted`
5. `config_sent`
6. `config_applied`
7. `ready`
8. `failed`

Transitions:

1. `spawned -> hello_received` on `LeafHello`.
2. `hello_received -> discovery_requested` by emitting `LeafDiscoveryRequest`.
3. `discovery_requested -> discovery_accepted` on `LeafDiscoveryAck(status=accepted)`.
4. `discovery_requested -> failed` on `LeafDiscoveryAck(status=rejected)` or timeout.
5. `discovery_accepted -> config_sent` by emitting `LeafConfigCard`.
6. `config_sent -> config_applied` on `LeafConfigAck(status=applied)`.
7. `config_sent -> failed` on `LeafConfigAck(status=rejected)` or timeout.
8. `config_applied -> ready` when runtime activation completes.

## Leaf startup state machine

States:

1. `booted`
2. `hello_sent`
3. `discovery_wait`
4. `discovery_done`
5. `config_wait`
6. `configured`
7. `ready`
8. `failed`

Transitions:

1. `booted -> hello_sent` by emitting `LeafHello`.
2. `hello_sent -> discovery_wait`.
3. `discovery_wait -> discovery_done` after local discovery check and `LeafDiscoveryAck(accepted)`.
4. `discovery_wait -> failed` with `LeafDiscoveryAck(rejected)`.
5. `discovery_done -> config_wait`.
6. `config_wait -> configured` on `LeafConfigCard` + local apply + `LeafConfigAck(applied)`.
7. `config_wait -> failed` on config apply error (`LeafConfigAck(rejected)`).
8. `configured -> ready` when command loop enters steady state.

## Sequence (nominal)

1. Root spawns worker process.
2. Leaf starts minimal runtime and sends `LeafHello`.
3. Root sends `LeafDiscoveryRequest` with required node subset for this worker.
4. Leaf runs discovery locally and replies `LeafDiscoveryAck(accepted)`.
5. Root sends `LeafConfigCard` (only required config slices).
6. Leaf applies config and replies `LeafConfigAck(applied)`.
7. Root marks worker `ready` and opens execution start barrier.

## Execution start and source pacing (runtime path)

After startup barrier opens, runtime execution follows these rules:

1. Root emits start-work command once per ready leaf worker.
2. Root does not synchronously wait for per-item boundary replies during normal data flow.
3. Leaf business traffic flows to root as boundary output stream.
4. Tombstone remains the authoritative completion signal for shutdown-readiness quorum.

### Source read advancement policy (leaf)

For pull-file sources in leaf workers:

1. Source pacing is controlled by `runtime.platform.source_ingress`:
   - `pacing_mode: batch` with `batch_size >= 1`, or
   - `pacing_mode: all`.
2. For `pacing_mode=batch`, start-work/bootstrap uses `single_shot=true` and emits exactly `batch_size`
   source pulls per trigger.
3. Next source pull trigger is emitted only on platform signal `sink_dispatch_ack`
   (successful outbound dispatch from leaf to root).
4. If sink-dispatch ack carries `tombstone_output=true`, no next-read trigger is emitted.
5. For `pacing_mode=all`, start-work/bootstrap uses `single_shot=false` and source self-rearms locally;
   ack-driven next-read is disabled.

This keeps source pacing event-driven and bounded by outbound flow, instead of draining
the entire file in one boundary invocation.

## Error and retry policy

1. Timeouts
- `hello_timeout_seconds`
- `discovery_timeout_seconds`
- `config_timeout_seconds`

2. Retry strategy
- idempotent re-send for request messages using `request_id`.
- max retries per stage configured by runtime.

3. Rejection handling
- on `status=rejected`, root marks worker/group failed and stops startup for dependent stages.

## Observability requirements for handshake

For each stage emit:

1. lifecycle log event
- `control_plane.handshake.<stage>.<status>`

2. latency metrics
- `control_plane.handshake.stage_duration_ms{stage,group,worker,status}`
- `control_plane.handshake.total_duration_ms{group,worker,status}`

3. runner gap metrics
- `runner.node.inter_activation_gap_ms{node_name,status}`

4. traces
- one parent startup span in root with child spans for each handshake stage and each worker.

## Compatibility and rollout

1. Protocol revision is explicit in discovery request.
2. Root must support compatibility mode:
- `legacy`: direct `leaf_hello -> leaf_config_card`.
- `v2`: `leaf_hello -> discovery_request/ack -> config_card/ack`.
3. Rollout strategy:
- phase switch via runtime flag, then make `v2` default.

## Out of scope for this phase

1. Replacing process spawn argument contract entirely in one step.
2. Transport backend parity across `ipc_local|tcp_local|zmq|redis` in the same phase.
3. Dynamic worker scaling protocol details.
