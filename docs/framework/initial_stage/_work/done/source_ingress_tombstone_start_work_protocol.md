# Source Tombstone + Root Settle Protocol

## Why this was introduced

In process-supervisor mode we observed nondeterministic completion after `start-work`:

- root could finish post-start settle before downstream payloads fully traversed groups;
- output/traces could be partially produced;
- behavior varied across runs because quiet-window settle only watched "recent activity", not explicit source completion.

This document defines the explicit completion signal for pull sources and the root-side settle gate.

## Protocol overview

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

### 3. Root reply ingress handling

Root `reply_ingress_service` intercepts boundary result outputs and:

- normalizes observability relay targets;
- keeps tombstone-marked business envelopes in normal handoff flow;
- tracks completion when leaf reports `tombstone_input=true` on completed boundary result;
- stores completion marker in control-plane state:
  - `kind=leaf_tombstone_completed`
  - `target_group`, `worker_id`, `request_id`, `tombstone_output`.

### 4. Root post-start settle gate

Root loop settle phase now requires tombstone completion once start-work targets were dispatched:

- required groups = business process groups (`runtime.platform.process_groups` excluding `system.*`);
- observed groups = `leaf_tombstone_completed.target_group` in control-plane state;
- quiet-window exit is blocked until:
  - all required groups are observed, or
  - settle max-wait timeout is reached.

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
- If some business group never reports tombstone completion, root exits settle only by max-wait timeout.
- Completion markers are tracked in control-plane state events, so diagnostics can identify missing groups.
- The protocol stays on platform routing rails and avoids extra synthetic routing targets.

## Test coverage

Implemented contract tests:

- source wrapper marks the last payload envelope with `tombstone=true`;
- root reply ingress captures leaf tombstone completion markers (including empty-output completion);
- root post-start settle waits for group-level tombstone completion;
- runner propagation preserves tombstone flag through local and boundary routing.

## Current limits and next steps

- Current marker is tied to pull source wrappers; push sources still need explicit terminal signaling strategy.
- Group completion is inferred from completed boundary results with tombstone input.
- Optional extension: explicit observability-lane completion aggregation for deterministic observability worker shutdown.
