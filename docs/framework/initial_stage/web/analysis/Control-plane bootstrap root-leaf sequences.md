# Control-plane bootstrap root/leaf sequences (concept)

## Purpose

Describe how control-plane bootstrap happens in **root** vs **leaf** processes,
with discovery and orchestration expressed as system nodes instead of hardcoded
`bootstrap.py` flows.

This is descriptive only; migration steps live in the transition plan.

Related docs:

- [Supervisor control-plane architecture](Supervisor%20control-plane%20architecture.md)
- [IPC transport and worker lifecycle services](IPC%20transport%20and%20worker%20lifecycle%20services.md)
- [Root async bootstrap with discovery and config streams](Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)
- [Platform discovery runtime stream scenario](Platform%20discovery%20runtime%20stream%20scenario.md)

## Terms

- **root process**: the owner process that runs `app.run` and orchestrates all workers.
- **leaf process**: a worker process spawned by the lifecycle service.
- **bootstrap pulse**: a single control-plane event used to start the bootstrap flow in a process.

## Core idea

Bootstrap is expressed as **system nodes** that run inside the runner.
The runner is always started immediately; what it does first depends on the
bootstrap pulse injected into its queue.

Discovery is not a background thread. It is an **adapter-driven stream of
events** sent through the runner so observability (logs/traces) captures startup.

## Root sequence (owner process)

**Pulse**: `control.cp.root_pulse` emitted from the `app.run` entrypoint.

1. `app.run` builds runtime artifacts and then injects the root pulse into runner bootstrap inputs.
2. Runner starts and executes `system.cp.root_bootstrap`:
   - calls `ControlPlaneBootstrapperService`.
   - the service invokes a discovery adapter, which emits **discovery events**
     (nodes, services, adapters, process groups).
   - the node forwards those events into the runner output stream.
3. `system.cp.discovery_collect` receives discovery events and stores them in a KV-backed registry (append-only).
4. When `discovery.completed` arrives, `system.cp.discovery_collect` emits
   `ControlPlaneInitEvent`.
5. `system.cp.init_plan` reads runtime config + discovery registry and produces a launch plan.
6. `system.cp.spawn_dispatch` issues spawn commands via the lifecycle service.
7. Subsequent nodes (bootstrapped/ready/status/gate) proceed as defined in the supervisor architecture.

## Leaf sequence (worker process)

**Pulse**: `control.cp.leaf_pulse` emitted at worker process start.

1. Worker process starts via lifecycle service; before runner loop begins, it enqueues the leaf pulse.
2. Runner starts and executes `system.cp.leaf_bootstrap`:
   - waits for `control.cp.group_config.card` from the IPC transport.
   - invokes the same discovery adapter, but **filtered by the config card**:
     it resolves only the requested node set from the discovery registry.
   - emits `control.cp.group_config.ack` once nodes are resolved.
3. `system.cp.worker_bootstrap` (or the same leaf bootstrap node) triggers Node Loading Service to activate the requested nodes.
4. `control.worker.ready` is emitted and sent back to the root process.

## Root/leaf handshake protocol (detailed)

The leaf bootstrap is a **handshake**, not a single message.
This prevents early config delivery before the leaf control-plane nodes are ready.

### Message flow

1. Leaf process receives `ControlPlaneLeafPulse`.
2. `system.cp.leaf_bootstrap` emits `ControlPlaneLeafHelloEvent`.
3. Root control-plane node receives `leaf_hello`, checks:
   - launch plan exists,
   - target group exists,
   - worker is expected / tracked in runtime state.
4. Root control-plane node emits `ControlPlaneLeafConfigCardEvent` for that worker.
5. Leaf control-plane node receives `leaf_config_card`, resolves requested nodes (subset discovery) and initiates node loading.
6. Leaf control-plane node emits `ControlPlaneLeafConfigAckEvent`.
7. Root control-plane ack node receives `leaf_config_ack` and appends state transition event(s) into the control-plane state store.

### Addressing rules

- Transport routing uses `target_group` (group-level addressing).
- Payload always carries `worker_id` (worker-level identity).
- Group-level service commands are fan-out expanded into per-worker commands by root control-plane logic.
- Each expanded command gets a concrete `worker_id`.

This keeps current routing simple while preserving a path to future direct worker addressing.

### Why lifecycle/transport do not create config cards

- **Lifecycle service** manages process spawn/stop and endpoint registration only.
- **IPC transport service** manages delivery only.
- **Control-plane nodes** decide *what* configuration to send and *when*.

This keeps orchestration policy out of infrastructure services.

## Event contracts (initial shape)

### `ControlPlaneLeafHelloEvent` (leaf -> root)

Purpose: leaf reports that the minimal control-plane runner is alive and ready to accept config.

Fields:

- `target_group: str` — logical group address used by root routing.
- `worker_id: str` — worker identity within the group.
- `pid: int | None` — leaf process pid (diagnostics and state correlation).
- `runner_profile: str | None` — optional early runner profile hint (`sync`/`async`/`auto`).
- `emitted_at_epoch_ms: int` — local emit timestamp.

### `ControlPlaneLeafConfigCardEvent` (root -> leaf)

Purpose: root assigns a concrete runtime subset to a specific worker.

Fields:

- `target_group: str` — logical group address.
- `worker_id: str` — worker recipient identity.
- `config_id: str` — idempotency key for config assignment / retries.
- `run_id: str` — current run id.
- `scenario_id: str` — scenario id.
- `group_name: str` — execution group name (usually same as `target_group`).
- `nodes: tuple[str, ...]` — node names to activate in this worker.
- `runner_profile: str | None` — requested runner profile (`sync`/`async`/`auto`).
- `worker_slot: int | None` — stable ordinal inside group (future scaling/sharding support).
- `config_revision: int` — monotonically increasing revision.
- `issued_at_epoch_ms: int` — root emit timestamp.

Notes:

- The card should not include full runtime config or transport endpoints.
- Runtime base config already arrives via child bootstrap bundle.
- Endpoint ownership belongs to lifecycle + transport services.

### `ControlPlaneLeafConfigAckEvent` (leaf -> root)

Purpose: leaf confirms config receipt/application status.

Fields:

- `target_group: str` — logical group address (for return path routing).
- `worker_id: str` — worker identity.
- `config_id: str` — the config card being acknowledged.
- `status: str` — `accepted` / `applied` / `rejected`.
- `resolved_nodes: tuple[str, ...]` — resolved/activated nodes when known.
- `error: str | None` — diagnostic reason on reject/failure.
- `emitted_at_epoch_ms: int` — leaf emit timestamp.

## Module layout (execution/orchestration)

Control-plane orchestration code should live under a dedicated package and be
split by process role as it grows:

- `stream_kernel.execution.orchestration.control_plane`
  - `root/` — root-only orchestration services (ingress, waiters, boundary/stop orchestration)
  - `leaf/` — leaf-side control-plane helpers (if kept at control-plane layer)
  - `system_nodes.py` — temporary aggregate for control-plane nodes during migration
  - `__init__.py` — public exports used by builder/tests

Lifecycle orchestration code should also be split by process role:

- `stream_kernel.execution.orchestration.lifecycle`
  - `root/` — root lifecycle orchestration, shutdown manager, planning helpers
  - `leaf/` — leaf runtime activation, command loop, runtime session helpers
  - `planning.py` / `system_nodes.py` — temporary aggregate modules to be reduced
  - `orchestration.py` — runtime lifecycle wrapper entrypoint

Legacy target to remove:

- `stream_kernel.execution.orchestration.child_bootstrap`
  - hardcoded child bootstrap glue to be replaced by leaf init nodes + lifecycle/leaf services.

Current extraction slice (next TDD phase):

- `stream_kernel.execution.orchestration.lifecycle.system_nodes`
  - lifecycle bridge nodes consuming control-plane spawn intents
- `stream_kernel.execution.orchestration.lifecycle.control_plane_lifecycle_service`
  - `@service` orchestration service that receives spawn requests from nodes
  - injects existing platform rails (`ExecutionWorkerLifecycleService`, `ExecutionIpcEndpointRegistry`, state store)
- no new custom port kind is introduced for spawn/endpoint access in this slice;
  the lifecycle orchestration service uses already-existing platform service/KV ports.

Design rule:

- Keep control-plane nodes as **small dataclass classes**.
- Avoid a single "supervisor class" with many `@node` methods (high risk of god object growth).
- Shared logic belongs in services + state stores injected through platform ports.
- Prefer `root/*` and `leaf/*` role-local modules once a package exceeds a few files.

## Root console lifecycle logging rail (supervisor-visible stdout)

Because dedicated observability service-process mode can leave the root process
transport-only for exporter adapters, the root process still needs a lightweight
operator-visible stdout logging rail for lifecycle/control-plane progress.

Current direction:

- root lifecycle/control-plane `@node` instances emit `LogMessage` for key events
  (spawn dispatch, group startup ready, shutdown progress)
- a root-only `system.lifecycle.log_dispatch` node consumes `LogMessage`
- the dispatch node delegates to a root `@service` that uses a non-blocking
  dispatch wrapper (`AsyncDispatchLoop`) and writes to stdout via a plain log sink

This keeps root diagnostics visible without reintroducing hardcoded bootstrap
print statements or blocking the main runner/control-plane path on `stdout`.

## Discovery adapter contract

The discovery adapter is a platform **adapter** (not a service) and supports:

- `discover_all()` for root flows.
- `discover_subset(node_names)` for leaf flows.

It emits events through the runner so that startup activity is observable.

## Why this replaces bootstrap hardcode

- `bootstrap.py` no longer needs to contain orchestration logic.
- Leaf worker runtime concerns are split further:
  - child-bundle projection per execution group (`lifecycle/leaf/child_bundle.py`);
  - child runtime bootstrap + boundary batch execution helpers (`lifecycle/leaf/worker_runtime.py`);
  - startup control command handshake loop (`lifecycle/leaf/command_loop_service.py`, `@service`);
  - transport/control loop remains a separate concern and will consume these helpers.
- Next dismantling target is `child_bootstrap.py`: move remaining child activation/bootstrap logic
  into `lifecycle/leaf/*` services + nodes, keeping process entrypoint thin and message-driven.

Current typed leaf control commands/events extracted for the loop:

- `ControlPlaneLeafConfigCardEvent` -> `ControlPlaneLeafConfigAckEvent`
- `ControlPlaneLeafStopCommand` -> `ControlPlaneLeafStopAckEvent`
- `ControlPlaneLeafBoundaryExecuteCommand` -> `ControlPlaneLeafBoundaryResultEvent`

Root side now has a companion orchestration service (`control_plane/root_leaf_command_service.py`)
that builds typed requests and waits for typed replies via the platform
`ControlPlaneReplyWaiterService` (state-backed in the current slice). The wait path now pumps
`control_plane/root_reply_ingress_service.py` so IPC replies are drained and routed through root
control-plane nodes before state lookup.

Current gap to a fully runnable root<->leaf control-plane path (explicitly tracked):

- root boundary execution service must dispatch typed leaf boundary commands through IPC and
  return `RoutingResult` from typed leaf results; (implemented as
  `control_plane/root_boundary_execution_service.py`; post-run external-delivery handoff bridge now
  wired in root runner path via `control_plane/root_boundary_handoff_service.py`, full in-run
  streaming/batching handoff semantics still pending)
- root stop orchestration service must use typed stop command/ack with timeout + fallback policy;
  (typed send/wait service implemented as `control_plane/root_stop_execution_service.py`; typed
  fallback wrapper implemented as `control_plane/root_shutdown_service.py`; now wired into root
  runtime lifecycle shutdown via `lifecycle/root_runtime_lifecycle_manager.py`; full runtime-path
  replacement of remaining ad-hoc shutdown orchestration is still pending)
- IPC receive path bridge is implemented as `control_plane/root_reply_ingress_service.py`, but still needs
  integration into the live root runtime loop/entry path (outside service-level tests);
- root startup handshake should wait for `ControlPlaneLeafConfigAckEvent(status=\"applied\")` across spawned
  workers before advancing runtime state; (implemented as
  `control_plane/root_startup_handshake_service.py`; wired into lifecycle system plan as
  `system.lifecycle.group_startup_wait`, full root runtime bootstrap entry integration still pending)
- root runtime execution path now prepares lifecycle spawn context via
  `control_plane/root_runtime_bootstrap_service.py` before entering runtime lifecycle-managed runner
  execution; real process IPC handshake test (`hello -> config -> ack(applied)`) now exists in
  `tests/stream_kernel/execution/orchestration/test_control_plane_real_spawn_ipc_handshake_e2e.py`.
- real process IPC boundary round-trip test (`boundary execute -> boundary result`) now exists in
  `tests/stream_kernel/execution/orchestration/test_control_plane_real_spawn_ipc_boundary_roundtrip_e2e.py`.
- real process IPC stop round-trip test (`stop command -> stop ack`) now exists in
  `tests/stream_kernel/execution/orchestration/test_control_plane_real_spawn_ipc_stop_roundtrip_e2e.py`.
- real process IPC stop-timeout fallback test (typed stop timeout -> lifecycle terminate fallback)
  now exists in
  `tests/stream_kernel/execution/orchestration/test_control_plane_real_spawn_ipc_stop_timeout_fallback_e2e.py`.
- real process full-flow control-plane test (startup handshake + boundary handoff + runtime-lifecycle
  shutdown) now exists in
  `tests/stream_kernel/execution/orchestration/test_control_plane_real_spawn_ipc_full_flow_e2e.py`.
- leaf worker loop needs full lifecycle/finalization handling beyond startup + control iterations.
- Start/stop flows are encoded as nodes + events.
- Root vs leaf differences are selected by **pulse type**, not by branching
  logic inside bootstrap code.

## Implementation boundaries

- Bootstrap pulses are injected by:
  - root: `app.run` / orchestration entrypoint,
  - leaf: worker process start.
- All downstream logic is expressed as nodes + services, not as imperative supervisor code.

## Test cases (TDD-first)

Root bootstrap:
- `CP-ROOT-01`: `system.cp.root_bootstrap` consumes `control.cp.root_pulse`,
  calls the bootstrapper service, and emits `discovery.item` events plus a
  single `discovery.completed`.
- `CP-ROOT-02`: `system.cp.discovery_collect` appends discovery items into
  KV store and emits `ControlPlaneInitEvent` once `discovery.completed` arrives.
- `CP-ROOT-03`: discovery collector stores a snapshot in KV and does not emit
  init when no discovery items were observed.

Leaf bootstrap:
- `CP-LEAF-01`: `system.cp.leaf_bootstrap` consumes `control.cp.leaf_pulse`
  and emits `ControlPlaneLeafHelloEvent`.
- `CP-LEAF-02`: root assign-config node consumes `ControlPlaneLeafHelloEvent`
  and emits `ControlPlaneLeafConfigCardEvent` only when launch plan/state allows assignment.
- `CP-LEAF-03`: leaf config-apply node consumes `ControlPlaneLeafConfigCardEvent`,
  resolves only the requested node names, and emits `ControlPlaneLeafConfigAckEvent`.
- `CP-LEAF-04`: root ack node consumes `ControlPlaneLeafConfigAckEvent`
  and appends runtime state transition events into the control-plane state store.

Integration wiring (later phases):
- `CP-WIRE-01`: root pulse is injected by `app.run` (owner process only).
- `CP-WIRE-02`: leaf pulse is injected by worker process startup path.
- `CP-WIRE-03`: control-plane nodes are loaded from `execution.orchestration.control_plane`
  package via builder system-plan wiring (no bootstrap hardcode routing logic).
- `CP-WIRE-04`: lifecycle bridge nodes are loaded from `execution.orchestration.lifecycle`
  package via builder/child bootstrap system-plan wiring.

Lifecycle bridge (extraction slice):
- `CP-LC-01`: lifecycle spawn-dispatch node consumes `ControlPlaneSpawnRequestedEvent`
  and delegates to lifecycle orchestration service (no bootstrap hardcoded spawn loop call).
- `CP-LC-02`: lifecycle orchestration service appends spawn request intent to control-plane state
  and can resolve worker endpoints via injected endpoint registry KV port.
- `CP-LC-03`: end-to-end handshake test covers root pulse -> plan -> spawn request dispatch +
  leaf hello/config/apply/ack -> root state update.
