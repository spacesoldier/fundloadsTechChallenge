# Ring Data-Plane Bootstrap (Root -> Leaf Pipe Wiring)

Date: 2026-03-18
Status: implemented (on-graph ring planning + endpoint passing + KV-backed ring state)

## Goal

Build a deterministic ring data-plane plan from `runtime.platform.process_groups` and
pass direct worker-to-worker pipe endpoints into leaf processes at spawn time.

This stage is about wiring and route-map preparation. It does not require changing
business node logic in the same step.

## Topology Extraction

Input:
- `runtime.platform.execution_ipc.data_plane_topology` (`ring` or default `star`)
- ordered `runtime.platform.process_groups`

Rules:
- only `ring` mode enables direct worker links
- worker order is deterministic: process-group order, then worker slot order
- observability group (`system.observability`) is excluded from ring data-plane

For workers `(w1, w2, ..., wN)` in ring mode:
- build links `(w1->w2), (w2->w3), ..., (wN->w1)`
- each link gets a stable target id:
  `ring:{from_worker_id}->{to_worker_id}:data`

## How Pipes Are Passed To Leaf Processes

The platform already supports passing IPC endpoints via `spawn_worker(...)`:
- root allocates endpoints before `Process.start()`
- child endpoints are inserted into worker args at `child_endpoint_position`
- leaf bootstrap binds received endpoints into local IPC adapter registry

Ring endpoints use explicit endpoint keys:
- `target::{target_id}`

Example:
- key: `target::ring:execution.ingress#1->execution.features#1:data`
- leaf binder attaches this endpoint directly under the same `target_id`

This avoids lane normalization collisions and allows explicit direct addressing.

## Startup Flow

1. `system.cp.dag_assembly` emits `ControlPlaneDagAssembledEvent`.
2. `system.cp.init_plan` calls ring-topology service (DI), which:
   - builds ring links
   - allocates endpoints
   - persists plan + per-worker endpoint map in KV store.
3. `system.cp.spawn_dispatch` spawns workers.
4. Lifecycle spawn pulls per-worker `extra_child_endpoints` from ring service store.
5. Worker lifecycle merges those endpoints into the child endpoint bundle.
6. Leaf process binds all endpoints:
   - standard lane keys -> `compose_execution_ipc_worker_target_id(worker_id, lane=...)`
   - `target::...` keys -> direct target id binding

## Why This Is Safe

- Default mode remains `star` (no behavior change unless `ring` is enabled).
- Ring endpoints are additive to existing control/data/trace/log/metric lane endpoints.
- Endpoint passing uses existing `multiprocessing` spawn argument mechanics.
