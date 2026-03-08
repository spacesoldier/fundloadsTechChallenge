# Source Tombstone + Root Settle Protocol

## Why this was introduced

In process-supervisor mode we observed nondeterministic completion after `start-work`:

- root could finish post-start settle before downstream payloads fully traversed groups;
- output/traces could be partially produced;
- behavior varied across runs because quiet-window settle only watched "recent activity", not explicit source completion.

This document defines the explicit completion signal for pull sources and the root-side settle gate.

## Protocol overview (v2: prepare + ready-to-stop)

### 1. Start-work command

After root confirms all leaf workers are ready, it dispatches `start-work` to selected source targets (currently broadcasted to workers, then routed locally).

This starts pull ingress wrappers but does **not** itself mean source stream is finished.

### 2. Pull ingress emission contract (in-band)

`PullIngressSourceNode` emits tombstone as envelope metadata on the last business item:

- for each payload item:
  - emits business envelope;
  - self-reschedules with `BootstrapControl(target=<same source node>)` while source is not exhausted.
- for the last payload only:
  - emits normal business envelope with `tombstone=true`;
  - no separate synthetic payload is produced.
- after last payload is emitted, subsequent source calls return no envelopes.

### 3. Root tombstone observation and prepare phase

Root `reply_ingress_service` intercepts boundary result outputs and:

- normalizes observability relay targets;
- keeps tombstone-marked business envelopes in normal handoff flow;
- tracks tombstone observation on completed boundary results;
- stores completion marker in control-plane state:
  - `kind=leaf_tombstone_completed`
  - `target_group`, `worker_id`, `request_id`, `tombstone_output`.

Root shutdown-readiness service now emits **prepare** once (idempotent) when:

- all expected business groups are known;
- each expected group has observed tombstone completion.

Root then dispatches `ControlPlaneLeafShutdownPrepareCommand` to each live worker in
those groups.

### 4. Leaf readiness-to-stop gate (two-factor)

Leaf no longer emits drain-ready on tombstone alone.

Leaf shutdown-readiness service emits `ControlPlaneLeafDrainReadyEvent` only when:

1. leaf has observed tombstone completion (`tombstone_input=true` or `tombstone_output=true` on completed boundary result),
2. and leaf has received `ControlPlaneLeafShutdownPrepareCommand` from root.

This is idempotent per worker/request and deduped in leaf KV state.

### 5. Root post-start settle gate

Root loop settle phase now requires tombstone completion once start-work targets were dispatched:

- required groups = business process groups (`runtime.platform.process_groups` excluding `system.*`);
- observed groups = `leaf_tombstone_completed.target_group` in control-plane state;
- quiet-window exit is blocked until shutdown-readiness is reached:
  - prepare phase emitted by root,
  - and all required groups reported `ControlPlaneLeafDrainReadyEvent`,
  - or settle max-wait timeout is reached.

Heartbeat debug logs include required/observed/missing tombstones.

## Configuration contract

### `runtime.platform.source_ingress`

- `emit_tombstone` remains accepted by validator for compatibility, but tombstone handling is now part of default pull-ingress behavior.

### `runtime.platform.runner_loop`

- `require_source_tombstones` remains accepted by validator for compatibility, but root settle uses tombstone completion gating when start-work is active.

Related existing knobs:

- `post_start_settle_enabled`
- `post_start_settle_max_wait_seconds`
- `post_start_settle_quiet_window_seconds`

## Safety and behavior notes

- Tombstone does not introduce side-channel payload types: it is carried in standard business envelopes.
- If some business group never reports tombstone completion, root never emits prepare and exits settle only by max-wait timeout.
- If prepare is emitted but some group never reports drain-ready, root exits settle only by max-wait timeout.
- Completion markers are tracked in control-plane state events, so diagnostics can identify missing tombstone groups and missing ready groups.
- The protocol stays on platform routing rails and avoids extra synthetic routing targets.

## Test coverage

Implemented contract tests (updated for v2):

- source wrapper marks the last payload envelope with `tombstone=true`;
- root readiness service emits prepare only after all expected groups observed tombstone;
- leaf readiness service emits drain-ready only after both conditions (tombstone + prepare);
- root post-start settle waits for shutdown-ready (not tombstone-only progress);
- runner propagation preserves tombstone flag through local and boundary routing.

## Current limits and next steps

- Current marker is tied to pull source wrappers; push sources still need explicit terminal signaling strategy.
- Group completion is inferred from completed boundary results with tombstone input.
- Optional extension: explicit observability-lane completion aggregation for deterministic observability worker shutdown.
