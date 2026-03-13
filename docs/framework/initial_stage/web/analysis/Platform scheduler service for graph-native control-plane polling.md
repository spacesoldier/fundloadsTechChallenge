# Platform scheduler service for graph-native control-plane polling

## Why this was introduced

Control-plane source polling for IPC lanes was temporarily hardcoded in `AsyncRunner`
via control-plane-specific logic (`_enqueue_control_plane_poll_bootstraps`).

This created two issues:

- runner knew concrete control-plane node naming (`source:system.cp.*`);
- scheduling policy was not represented as DI services/nodes.

## Target model

Move scheduling policy into platform DI:

- `@service`: `PlatformSchedulerService` (KV-backed job store);
- `@node`: scheduler command node (upsert/cancel jobs);
- `@node`: scheduler tick node (emit due messages into graph).

Control-plane bootstraps (root/leaf) now configure scheduler jobs instead of
directly enqueuing source-bootstrap envelopes.

## Implemented components

### Platform service

`src/stream_kernel/platform/services/runtime/platform_scheduler.py`

- `PlatformSchedulerStore` (KV marker)
- `PlatformSchedulerUpsertCommand`
- `PlatformSchedulerCancelCommand`
- `PlatformSchedulerTickEvent`
- `PlatformSchedulerService` protocol
- `InMemoryPlatformSchedulerService` implementation

Behavior:

- jobs are persisted in KV (`platform.scheduler.jobs`);
- deterministic due-order by `job_id`;
- `dispatch_due()` returns due dispatches and advances `next_due_monotonic`;
- no drops/retries/backoff in normal path.

### Graph nodes

`src/stream_kernel/execution/orchestration/scheduler_system_nodes.py`

- `system.scheduler.command`: applies upsert/cancel commands
- `system.scheduler.tick`: consumes `PlatformSchedulerTickEvent`, emits targeted envelopes

### Control-plane wiring

- Root bootstrap node (`system.cp.root_reply_ingress_bootstrap`) emits
  `PlatformSchedulerUpsertCommand` per reply-ingress source lane.
- Leaf bootstrap node (`system.cp.leaf_command_ingress_bootstrap`) emits
  `PlatformSchedulerUpsertCommand` per command-ingress source lane.
- Source nodes stay pure polling nodes (`BootstrapControl` only) and do not consume pulse events.
- Control-plane plan includes scheduler nodes and scheduler command consumers in both root and leaf modes.

### Runner change

Runner no longer enqueues control-plane-specific poll bootstraps.

When queue is idle, runner emits generic `PlatformSchedulerTickEvent` targeted to
`system.scheduler.tick` (if such node exists). This keeps runner generic and shifts
source polling policy into scheduler DI layer.

## TDD coverage added

- `tests/stream_kernel/platform/services/runtime/test_platform_scheduler_service.py`
  - due dispatch/reschedule
  - cancel behavior
  - explicit payload pass-through
- `tests/stream_kernel/execution/orchestration/test_scheduler_system_nodes.py`
  - command + tick node integration
- updated control-plane bootstrap tests for root/leaf scheduler command emission
- updated planning tests to assert scheduler nodes/consumers are present
- updated async runner test to assert idle scheduler tick emission

## Current status

Phase completed: scheduler policy is now DI + graph-node based.

Remaining architectural work (separate phase): if required, move idle timer ownership
fully out of runner into a dedicated runtime scheduler loop/service entrypoint.
