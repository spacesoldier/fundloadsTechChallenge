# Control Plane Handoff Broadcast Contract

## Scope

This note fixes the runtime contract for control-plane fan-out dispatch done via
`ExecutionIpcHandoffDispatchService.dispatch_broadcast(...)`.

It is used to remove ad-hoc per-worker send loops from root system nodes and keep
delivery policy in one platform service (`node -> service -> adapter`).

## Service Contract

`dispatch_broadcast(envelope, target_group=None, include_observability=False, policy="best_effort", broadcast_id=None)`

- Input:
  - `envelope.target` defines lane/consumer intent.
  - `target_group` optionally limits recipients to one process-group.
  - `include_observability` controls whether `system.observability` workers are part of recipients.
  - `policy`: `best_effort` or `all_or_nothing`.
  - `broadcast_id` optional idempotency key for diagnostics.
- Output:
  - `BroadcastDispatchResult(broadcast_id, policy, total, accepted, failed, failed_workers)`.

## Recipient Selection

Recipients are derived from control-plane state:

1. Prefer workers with applied config ack (`ControlPlaneLeafConfigAckEvent`).
2. Fallback to launch plan workers (`ControlPlaneLaunchPlanEvent`) when applied set is empty.
3. Remove workers already acknowledged as stopped (`ControlPlaneLeafStopAckEvent`).
4. Apply `target_group` filter (if provided).
5. Exclude `system.observability` unless `include_observability=True`.

## Lane and Policy Rules

- Observability lane (`envelope.target` starts with `system.obs.`):
  - forced to `best_effort`,
  - no retry escalation.
- Control/data lanes:
  - retry count is controlled by `control_retry_attempts`,
  - `all_or_nothing` stops fan-out after first failed recipient and marks remaining as failed.

## Replay Isolation for Observability Lane

Observability envelopes are drained by handoff directly and do not enter replay requeue cycles.

- Root replay loop now executes a fast-path:
  - if pending external deliveries are observability-only and there is no replay-blocking inflight work,
  - dispatch them immediately and exit replay pass without enqueueing/re-running runner.
- This keeps observability background traffic from extending control/data replay windows.

## Worker-Aware Payload Projection

Each worker receives its own payload copy.

- `dict` payload:
  - injects defaults: `broadcast_id`, `worker_id`.
- dataclass payload:
  - if `worker_id` field exists: overwrite with recipient worker id,
  - if `command_id` contains `{worker_id}`: substitute with recipient worker id,
  - if `broadcast_id` field exists and empty: populate with broadcast id.

This makes typed control-plane commands broadcast-safe without mutating original payload.

## Current Runtime Usage

- `system.cp.start_work_dispatch` uses `dispatch_broadcast(...)` and no longer blocks on per-worker wait.
- Root shutdown path (`ControlPlaneRootRuntimeLifecycleManager`) now uses a two-phase stop flow:
  - phase 1: group-wise `dispatch_broadcast(...)` for `system.cp.leaf_stop`,
  - phase 2: per-worker ack wait + process join with `dispatch_command=False` in stop service.
  This removes per-worker command send loops while preserving typed ack validation.
- Root loop adds a post-start settle window after `ControlPlaneStartWorkEvent` dispatch:
  - repeatedly replays/drains boundary outputs and pumps reply ingress,
  - exits on configurable quiet window (or max wait),
  - prevents immediate supervisor shutdown before first cross-group business messages appear.

## Follow-up

- Stop/shutdown fan-out should use the same service path end-to-end, with
  worker-specific command templates and reply ingress handling remaining event-driven.
