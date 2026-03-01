# Supervisor control-plane architecture transition (TDD plan)

## Purpose

Refactor the multiprocess supervisor into explicit control-plane **system nodes**
plus supporting services and ports, while keeping execution logic inside worker
runners. This plan is the implementation counterpart to:

- [Supervisor control-plane architecture](../web/analysis/Supervisor%20control-plane%20architecture.md)
- [execution_ipc_tcp_bytes_transport_tdd_plan](execution_ipc_tcp_bytes_transport_tdd_plan.md)

## Scope

- Split process lifecycle, IPC transport, and control-plane coordination.
- Express supervisor logic as system nodes loaded during discovery.
- Preserve current deterministic behavior and output parity.
- Maintain memory profile as default fallback.

Non-goals:

- FastAPI integration
- Redis queue/topic adapters
- Multi-host transport

## Phase A — contract freeze (RED)

Add tests and contract docs for:

- `RuntimeLifecycleService` API (spawn/stop/join/terminate).
- `ExecutionIpcPort` API.
- Control-plane state store contract (in-memory baseline).
- Supervisor system node contracts (ready/start/stop, inflight/idle policy).

## Phase B — lifecycle service extraction (GREEN)

- Extract process management from `MultiprocessBootstrapSupervisor`.
- Add a platform service implementation with minimal tests.
- Supervisor delegates process spawn/stop to lifecycle service.
Detailed checklist:
- Add supervisor-level tests that assert lifecycle delegation:
  - `start_groups` calls `spawn_worker` once per worker.
  - `stop_groups` calls `stop_worker` and no longer calls internal terminate directly.
  - Supervisor does not create pipes/stop events itself (delegated to service).
  - Control pipe endpoint from lifecycle handle is used for control traffic.
- Ensure lifecycle service contract is sufficient:
  - `spawn_worker` returns handle with `process`, `stop_event`, `child_endpoint`.
  - `stop_worker` respects timeout and can terminate if needed.
- Keep registry logic in lifecycle service (not supervisor):
  - Endpoint registry populated by lifecycle service on spawn.
  - Supervisor only references returned handle.
Implementation notes:
- Add dependency injection in supervisor for `ExecutionWorkerLifecycleService`.
- Provide a safe fallback for direct instantiation (tests without DI).
- Preserve existing lifecycle events (`worker_spawned`, `worker_ready`, `worker_stopped`).
Status:
- done: `LocalExecutionWorkerLifecycleService` implemented with spawn/endpoint registry + stop_event
  injection + stop signaling tests (`tests/stream_kernel/platform/services/runtime/test_worker_lifecycle_service.py`).
- done: supervisor delegates spawn/stop to lifecycle service
  (`tests/stream_kernel/platform/services/runtime/test_bootstrap_supervisor_lifecycle_delegation.py`).

## Phase C — transport service extraction (GREEN)

- Introduce IPC transport service that owns IPC ports.
- Pipe-based adapters as baseline implementation.
- Supervisor delegates send/recv to transport ports.
Status:
- done: IPC transport service + pipe adapter baseline (platform services/ipc).
- done: supervisor dispatch and bootstrap drains use IPC transport service
  (`tests/stream_kernel/platform/services/runtime/test_bootstrap_supervisor_ipc_transport_delegation.py`).

## Phase D — control-plane coordinator (GREEN)

- Implement state store + supervisor system nodes:
  - bootstrap bundle exchange
  - ready and start transitions
  - inflight tracking
  - idle/stop policy evaluation
- Use in-memory state store.
Status:
- partial: control-plane state store + init/plan node implemented
  (`src/stream_kernel/platform/services/runtime/control_plane_state.py`,
  `src/stream_kernel/execution/orchestration/control_plane_system_nodes.py`,
  `tests/stream_kernel/platform/services/runtime/test_control_plane_state_service.py`,
  `tests/stream_kernel/execution/orchestration/test_control_plane_init_plan_node.py`).
- partial: root bootstrap + discovery collector nodes implemented
  (`src/stream_kernel/platform/services/runtime/control_plane_bootstrapper.py`,
  `src/stream_kernel/platform/services/runtime/control_plane_discovery.py`,
  `src/stream_kernel/execution/orchestration/control_plane_system_nodes.py`,
  `tests/stream_kernel/execution/orchestration/test_control_plane_root_bootstrap_node.py`,
  `tests/stream_kernel/execution/orchestration/test_control_plane_discovery_collect_node.py`).

## Phase E — integration wiring (GREEN)

- Control-plane runner loads supervisor system nodes via discovery.
- Nodes use injected lifecycle/transport/state services.
- Remove direct `Pipe` usage from supervisor.
- Preserve existing boundary dispatch contract and diagnostics.
Status:
- partial: root pulse injected into runtime bootstrap inputs for process-supervisor mode
  (`tests/stream_kernel/execution/orchestration/test_builder.py`,
  `src/stream_kernel/execution/orchestration/builder.py`).
- partial: leaf pulse injected into runtime bootstrap inputs for worker processes
  (`tests/stream_kernel/execution/orchestration/test_builder.py`,
  `src/stream_kernel/execution/orchestration/builder.py`).
- partial: control-plane system nodes wired for root/leaf via build_runtime_artifacts
  (`tests/stream_kernel/execution/orchestration/test_builder.py`,
  `src/stream_kernel/execution/orchestration/control_plane_system_nodes.py`,
  `src/stream_kernel/execution/orchestration/builder.py`).
- partial: control-plane orchestration extracted into `execution.orchestration.control_plane`
  package with compatibility shim; leaf bootstrap emits `leaf_hello`
  (`src/stream_kernel/execution/orchestration/control_plane/system_nodes.py`,
  `tests/stream_kernel/execution/orchestration/test_control_plane_leaf_bootstrap_node.py`).

## Phase F — regression and parity

- Existing multiprocess tests remain green.
- Boundary dispatch and lifecycle tests remain green.
- Performance baseline within expected range.

## Done criteria

- Supervisor logic is implemented as system nodes.
- IPC channels are accessed via platform ports.
- Control-plane state + policy is isolated and test-covered.
- No regression in current multiprocess runs.
