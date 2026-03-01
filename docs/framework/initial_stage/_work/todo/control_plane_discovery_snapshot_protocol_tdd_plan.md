# Control-plane discovery snapshot protocol TDD plan

## Test cases (write first)

### A. Event contracts

1. `ControlPlaneLeafDiscoverySnapshotEvent` accepts valid payload.
2. `ControlPlaneLeafDiscoverySnapshotEvent` rejects invalid `required_nodes`.
3. `ControlPlaneLeafDiscoverySnapshotEvent` rejects invalid `snapshot_records`.
4. IPC codec encodes/decodes snapshot event in `bytes` mode.

### B. Leaf snapshot apply service (service + adapter)

1. Given valid snapshot and resolvable modules, service returns `discovery_ack(status=accepted)`.
2. Given missing required node in snapshot, service returns `discovery_ack(status=rejected, missing_nodes=...)`.
3. Given import/qualname resolve failure, service returns rejected ack with error details.
4. Service appends snapshot records to discovery registry through `ControlPlaneDiscoveryService`.

### C. Leaf node wiring

1. `system.cp.leaf_snapshot_apply` consumes snapshot event and emits discovery ack.
2. Planning for leaf role includes snapshot node + consumer mapping.
3. Leaf ingress forwards produced discovery ack to root.

### D. Root reply ingress behavior

1. With `startup_protocol_revision=3`, `leaf_hello` triggers `leaf_discovery_snapshot` send.
2. Snapshot payload is scoped to target group required nodes.
3. If snapshot build is unavailable, root falls back to v2 discovery request (no startup deadlock).

### E. End-to-end startup path (targeted)

1. Root rev3 + one worker group:
   - hello -> snapshot -> discovery_ack accepted -> config_card -> config_ack applied
2. Rejected discovery ack fails readiness deterministically.

---

## Phase plan

## Phase A (now): protocol skeleton

- Add snapshot event contract.
- Add root snapshot builder service.
- Add leaf snapshot apply service + verification adapter.
- Add leaf snapshot node and planning mapping.
- Wire root reply ingress for rev3 path.

## Phase B: codec + compatibility

- Add IPC codec support for snapshot event.
- Keep rev2 fallback path explicitly covered by tests.

## Phase C: e2e hardening

- Add startup handshake e2e for rev3.
- Validate readiness/timeout semantics and shutdown behavior.

## Phase D: optimization and payload shape

- Minimize snapshot payload (per-group records only).
- Optional dependency closure enrichment (services/adapters) behind config flag.
