# Platform discovery runtime stream scenario

## Purpose

Describe the target **platform-rail** discovery flow where startup discovery is
executed inside the runner (`@node -> @service -> port -> @adapter`) and no
build-time hidden orchestration is required beyond minimal pulse bootstrap.

Related:

- [Root async bootstrap with discovery and config streams](Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)
- [Control-plane bootstrap root-leaf sequences](Control-plane%20bootstrap%20root-leaf%20sequences.md)
- [Supervisor control-plane architecture](Supervisor%20control-plane%20architecture.md)

## Scope and non-goals

In scope:

- root discovery as message-driven streaming flow;
- two discovery adapters (platform modules and project modules);
- registry population through platform stores (ports), not local dict caches;
- deterministic stream ordering and completion semantics;
- DAG assembly handoff from discovery registries.

Out of scope:

- changing business node contracts;
- transport backend changes (IPC/TCP/ZMQ/Redis);
- replacing runtime config stream (already covered by separate flow).

## Minimal bootstrap kernel (kept)

Only this remains outside discovery flow:

1. start async runner;
2. enqueue `ControlPlaneRootPulse`;
3. register minimal control-plane startup nodes/services.

Everything else is runner-driven and observable.

## Discovery roles

### Node layer

`system.cp.root_bootstrap`:

- receives `ControlPlaneRootPulse`;
- emits typed discovery command event (start signal), not module traversal itself.

`system.cp.discovery_pump`:

- receives batch-ready event;
- emits item events and (if needed) next-batch request event.

`system.cp.discovery_registry_apply`:

- consumes discovery item events;
- calls registry service to persist entities in platform stores.

`system.cp.discovery_finalize`:

- consumes discovery completed event;
- emits `ControlPlaneDiscoveryCompletedEvent`.

### Service layer

`ControlPlaneDiscoveryStreamService`:

- owns discovery session state (cursor/batch progression);
- requests batches from adapters through discovery port contracts;
- merges platform/project batches in deterministic order;
- emits next control-plane event payloads.

`ControlPlaneDiscoveryRegistryService`:

- validates entity shape;
- writes entity descriptors into typed registries;
- tracks stats/checkpoints (`seen`, `duplicates`, `source`).

### Port contracts

`ControlPlaneDiscoveryPort`:

- `start_session(runtime) -> DiscoverySession`;
- `next_batch(session_id, source, limit) -> DiscoveryBatch`;
- `close_session(session_id)`.

`ControlPlaneDiscoveryRegistryPort`:

- `append_entity(entity)`;
- `list_entities(kind)`;
- `snapshot()`.

### Adapter layer

Two adapters behind the same discovery port:

1. `platform_discovery_adapter`:
   - scans framework/platform modules only.
2. `project_discovery_adapter`:
   - scans user/project modules only.

Both adapters produce the same entity envelope contract.

## Entity stream contract

`DiscoveryEntityRecord` (typed event payload):

- `entity_kind`: `node | service | adapter`;
- `entity_id`: stable id (`module_path:qualified_name` or explicit declared name);
- `source_scope`: `platform | project`;
- `module`: module path;
- `qualname`: python qualname;
- `meta`: typed descriptor map (consumes/emits/binds/service contracts/etc).

Ordering rule:

1. `platform` records first;
2. then `project`;
3. within each scope: lexicographic `(module, qualname)`.

This guarantees deterministic registry content for equal code/config input.

## Stream progression without node-side loops

To avoid loops/generators in node code and avoid deep recursion in adapters:

1. `root_bootstrap` emits `DiscoveryStartRequested`.
2. `discovery_pump` calls service `start(...)` and emits first `DiscoveryBatchRequested`.
3. Service asks adapter for `next_batch(limit=N)` and emits `DiscoveryBatchReady`.
4. `discovery_pump` expands batch into `DiscoveryEntityRecord` events and emits:
   - next `DiscoveryBatchRequested` when `has_more=True`;
   - `DiscoverySourceCompleted` when source exhausted.
5. After both sources are completed, service emits `DiscoveryCompleted`.

Runner event loop provides iteration naturally; no recursive adapter traversal API is needed.

## Adapter traversal model

Adapter traversal should be iterative with explicit frontier queue:

- initialize frontier with root package modules;
- pop one module path at a time;
- inspect declarations;
- enqueue discovered submodules;
- return records in fixed-size batches (`limit`, default from runtime config).

This avoids deep recursion and keeps memory bounded.

## Registry population model

`system.cp.discovery_registry_apply` + registry service writes to platform stores:

- `NodeRegistryStore`;
- `ServiceRegistryStore`;
- `AdapterRegistryStore`;
- optional `DiscoveryStatsStore`.

No service-owned dict caches as source of truth. In-memory dict can be internal
temporary buffer only if persisted through the store port on every mutation.

## DAG assembly handoff

If DAG assembly remains in runtime startup:

1. wait for:
   - `ControlPlaneDiscoveryCompletedEvent`;
   - `ControlPlaneConfigStreamCompletedEvent`.
2. call `DagAssemblyService` using registries + config stores;
3. persist DAG plan in `DagRegistryStore`;
4. emit `ControlPlaneInitEvent` / launch-plan event.

This keeps DAG derivation deterministic and replayable.

## Runtime config knobs (platform section)

Suggested additions under `runtime.platform.discovery`:

- `batch_size` (default e.g. `128`);
- `platform_modules` include/exclude roots;
- `project_modules` include/exclude roots;
- `max_items` safety cap;
- `emit_stats_every_batches`.

Defaults stay in code; config only overrides.

## Failure model

On adapter/service failure:

- emit `DiscoveryFailedEvent` with source and cursor position;
- stop startup barrier progression;
- keep registry snapshot for diagnostics;
- fail fast with deterministic error category.

## Migration target

After this scenario is implemented end-to-end:

- remove build-time discovery dependency from startup orchestration path;
- keep builder only as artifact/wiring constructor;
- make discovery fully observable in root control-plane stream.
