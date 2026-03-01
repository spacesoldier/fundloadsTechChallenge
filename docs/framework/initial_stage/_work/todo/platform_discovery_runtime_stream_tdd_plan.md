# Platform discovery runtime stream TDD plan

## Scope

Implement discovery as a runtime platform flow:

- `@node -> @service -> port -> @adapter`;
- dual adapters (`platform` and `project`);
- batch streaming events without node-side loops/recursion;
- registry population through platform stores;
- deterministic handoff into DAG assembly.

Reference analysis:

- [Platform discovery runtime stream scenario](../../web/analysis/Platform%20discovery%20runtime%20stream%20scenario.md)
- [Root async bootstrap with discovery and config streams](../../web/analysis/Root%20async%20bootstrap%20with%20discovery%20and%20config%20streams.md)

## Status

- Phase A: completed
- Phase B: completed
- Phase C: completed
- Phase D: completed
- Phase E: completed

## Rules

1. Docs -> tests -> code.
2. No compatibility shims for removed bootstrap hardcode path.
3. Deterministic ordering is mandatory.
4. Services persist via platform store ports, not service-owned dict source-of-truth.

## Phase A — Discovery stream contracts (events + validation)

Goal:

- define typed contracts for discovery session/batch/entity flow.

Tests:

1. `DiscoveryEntityRecord` validates `entity_kind`, `source_scope`, ids and metadata mapping.
2. `DiscoveryStartRequested` validates runtime payload.
3. `DiscoveryBatchRequested` validates cursor/limit.
4. `DiscoveryBatchReady` validates `has_more/next_cursor` and entity tuple.
5. deterministic sort helper orders records by `source_scope -> module -> qualname`.

Code target:

- `platform/services/runtime/control_plane_events.py`

## Phase B — Discovery stream service + ports

Goal:

- create `ControlPlaneDiscoveryStreamService` that manages sessions and batches.

Tests:

1. service opens discovery session and emits first batch request.
2. service merges platform/project streams deterministically.
3. service emits source-completed and final discovery-completed events.
4. service is idempotent on duplicate batch/source-completed events.

Code target:

- `platform/services/runtime/control_plane_discovery_stream.py` (new)

## Phase C — Adapter implementations

Goal:

- implement iterative module traversal adapters for platform and project scopes.

Tests:

1. adapters emit only their scope entities.
2. traversal is iterative (bounded batch output, no deep recursion dependence).
3. stable output ordering for equal module tree input.

Code target:

- `platform/services/runtime/*discovery*adapter*` (new or split files)

## Phase D — Root control-plane node wiring

Goal:

- switch root discovery node to discovery stream command flow.

Tests:

1. root bootstrap emits `DiscoveryStartRequested`.
2. pump/apply/finalize nodes progress through batches to completed event.
3. startup barrier opens on new discovery-completed signal.

Code target:

- `execution/orchestration/control_plane/root/system_nodes.py`
- `execution/orchestration/control_plane/planning.py`

## Phase E — Registry persistence + DAG handoff

Goal:

- persist discovered entities to typed registries and build DAG from stores.

Tests:

1. registry stores (`node/service/adapter`) receive persisted records.
2. DAG assembly reads from registry stores, not builder-time context discovery.
3. root startup chain emits launch plan from registry-backed assembly.

Code target:

- control-plane registry services
- lifecycle planning / DAG assembly service

## Exit criteria

1. Discovery no longer depends on pre-run builder module crawling.
2. Root startup discovery is fully message-driven and test-covered.
3. Registry data and DAG assembly are deterministic for the same code/config input.
4. No hidden bootstrap orchestration remains outside minimal pulse kernel.
