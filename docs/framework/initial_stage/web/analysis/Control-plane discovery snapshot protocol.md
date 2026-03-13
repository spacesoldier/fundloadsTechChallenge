# Control-plane discovery snapshot protocol

## Goal

Replace leaf-side full discovery scan during startup with a root-produced discovery snapshot,
while preserving contract validation and deterministic startup behavior.

This removes redundant module crawling in each leaf and keeps startup protocol explicit:

1. `leaf_hello`
2. `leaf_discovery_snapshot`
3. `leaf_discovery_ack`
4. `leaf_config_card`
5. `leaf_config_ack`

## Why this change

Current v2 startup asks each leaf to perform `discover_all()` locally.  
That duplicates expensive scans and makes startup sensitive to per-process discovery configuration.

Snapshot protocol shifts discovery ownership to root:

- root runs discovery once
- root sends minimal per-group snapshot card
- leaf verifies snapshot availability locally and acks

## Protocol (revision 3)

### Root -> Leaf: `ControlPlaneLeafDiscoverySnapshotEvent`

Payload contract:

- `target_group`
- `worker_id`
- `request_id`
- `required_nodes`
- `snapshot_records` (`ControlPlaneDiscoveryEntityRecord[]`)
- `protocol_revision`

Snapshot includes records for nodes needed by the specific execution group (optionally extended later to service/adapter dependencies).

### Leaf processing

`system.cp.leaf_snapshot_apply` node:

1. passes event to snapshot-apply service
2. service stores snapshot records into `ControlPlaneDiscoveryService`
3. service delegates verification to snapshot-verification adapter
4. service returns `ControlPlaneLeafDiscoveryAckEvent`

### Leaf -> Root: `ControlPlaneLeafDiscoveryAckEvent`

Status:

- `accepted` when required nodes are resolvable from snapshot + local import resolution
- `rejected` with `missing_nodes` / `error` otherwise

### Root behavior after ack

Unchanged from v2:

- on accepted discovery ack -> send `leaf_config_card`
- on rejected discovery ack -> mark readiness failure and stop startup

## Platform rails mapping

- **Node**: `system.cp.leaf_snapshot_apply`
- **Service**: `ControlPlaneLeafDiscoverySnapshotApplyService`
- **Port/Adapter**: `ControlPlaneDiscoverySnapshotVerificationAdapter`
- **Root Service**: `ControlPlaneRootDiscoverySnapshotService` (builds snapshot cards from state + discovery registry)

## Compatibility strategy

- rev1: hello -> config_card (legacy)
- rev2: hello -> discovery_request -> discovery_ack -> config_card
- rev3: hello -> discovery_snapshot -> discovery_ack -> config_card

Default runtime behavior now targets **rev3**.

Fallback knobs:

- `runtime.platform.control_plane.discovery_request_fallback: bool`  
  Default: `false`.  
  When `true`, root may fallback from rev3 snapshot to rev2 discovery request if snapshot is unavailable.

Leaf runtime local discovery fallback is removed: activation now relies only on control-plane snapshot/discovery data.

## Non-goals (phase 1)

- Cross-process transfer of full config tree
- Dependency closure for services/adapters beyond required node set
- Runtime hot-reload of snapshots
