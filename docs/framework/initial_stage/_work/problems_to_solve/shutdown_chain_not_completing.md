# Problem: Graceful Shutdown Chain — Workers Not Stopping

**Observed symptom:** after all data is processed and tombstones propagate,
the system does not shut down cleanly. Workers exit via forced OS termination
(`stop_command_timed_out=True`, `fallback_used=True`) or do not exit at all
(baseline runs show no shutdown events in logs).

---

## Shutdown Chain — Full Call Path

```
Source exhausts input
  → emits Envelope(tombstone=True) into work queue

Leaf: ControlPlaneLeafBoundaryExecuteNode.__call__()   leaf/system_nodes.py
  → detects tombstone_input=True on processed envelope
  → emits ControlPlaneLeafBoundaryOutputsEvent(tombstone_output=True, source_target=...)

Leaf: ControlPlaneLeafTombstoneFinalizeNode.__call__()
  → receives ControlPlaneLeafBoundaryOutputsEvent
  → (if source_target present) skips — waits for sink ACK instead
  → receives ControlPlaneLeafSinkDispatchAckEvent(tombstone_output=True)
  → calls readiness.observe_sink_dispatch_ack() or observe_boundary_outputs()
  → emits ControlPlaneLeafDrainReadyEvent

Leaf: ControlPlaneLeafReplyDispatchNode.__call__()
  → dispatches ControlPlaneLeafDrainReadyEvent to root via IPC control lane

Root: ControlPlaneRootLeafIngressSourceNode
  → polls IPC control lane
  → routes ControlPlaneLeafDrainReadyEvent → "system.cp.shutdown_leaf_ready"

Root: ControlPlaneRootLeafDrainReadyNode.__call__()
  → calls readiness_service.mark_leaf_ready(event)
  → when all expected groups ready → emits ControlPlaneShutdownReadyEvent

Root: ControlPlaneRootStopNode.__call__()           root/system_nodes.py
  → receives ControlPlaneShutdownReadyEvent
  → emits ControlPlaneRootLeafStopRequestEvent for each spawned worker
  → emits PlatformSchedulerCancelCommand for each ingress job
  → calls runner_control.request_stop()

Root: ControlPlaneRootLeafStopDispatchNode
  → sends ControlPlaneLeafStopCommand to each leaf via IPC

Leaf: ControlPlaneLeafStopNode.__call__()
  → receives ControlPlaneLeafStopCommand
  → emits ControlPlaneLeafStopAckEvent
  → calls runner_control.request_stop()
  → AsyncRunner.run_until_stopped() exits loop

LocalExecutionWorkerLifecycleService.stop_worker()  worker_service.py
  → sets stop_event
  → joins process with graceful_timeout_seconds=120
  → on timeout: terminate() → kill()
```

---

## Four Specific Problems

### P-1. `ControlPlaneRootStopNode` only stopped workers in `expected_groups`

```python
# Before fix — in ControlPlaneRootStopNode.__call__():
for group_name, worker_id in _spawned_workers_for_shutdown(
    events=events,
    expected_groups=payload.expected_groups,   # ← filtered by shutdown-ready groups
):
```

`ControlPlaneShutdownReadyEvent.expected_groups` contains only the non-exempt groups
(business workers). `system.observability*` is excluded from readiness tracking via
`_is_readiness_exempt_group_name`. But the stop dispatch was also filtered by the
same set — so the observability worker never received a `ControlPlaneLeafStopCommand`
and never called `runner_control.request_stop()`.

The same filter applied to `_root_leaf_ingress_scheduler_cancel_commands`, so the
root-side ingress scheduler jobs for the observability process were also not cancelled.
The ingress poller kept running after all business workers had stopped.

Result: the root runner could not exit because the ingress source job for the
observability worker was still scheduled and the worker process was still alive.

---

### P-2. `ControlPlaneLeafTombstoneFinalizeNode` declared drain-ready too early

The node previously only consumed `ControlPlaneLeafBoundaryOutputsEvent`. This event
is emitted as soon as boundary execution finishes — before the tombstone envelope is
dispatched to the downstream sink. In the ring topology, the sink is another leaf
process. The `ControlPlaneLeafBoundaryOutputsEvent` arrives at the finalize node
before the envelope has crossed the IPC pipe to the next stage.

Declaring drain-ready at this point means the root may emit `ControlPlaneShutdownReadyEvent`
while downstream stages are still processing. If the next stage's tombstone arrives
after the root has already started the stop sequence, that stage has no chance to
signal its own drain-ready before being forcibly terminated.

---

### P-3. `terminate_timeout_seconds` parameter was silently ignored

```python
# Before fix — in LocalExecutionWorkerLifecycleService.stop_worker():
_ = terminate_timeout_seconds   # ← assigned to _ and discarded
if handle.stop_event is not None:
    setter = getattr(handle.stop_event, "set", None)
    if callable(setter):
        setter()
if process.is_alive():
    process.join(timeout=max(0.0, float(graceful_timeout_seconds)))
if process.is_alive():
    return False          # ← process still alive, returns False but does NOT kill
```

When `ControlPlaneLeafStopCommand` was not ACKed within the lifecycle manager's
`stop_command_timeout_seconds=1.0`, the process was joined for up to
`graceful_timeout_seconds=120`. If it was still alive after 120s, `stop_worker`
returned `False` but did nothing further. No `terminate()`, no `kill()`.

In practice the IPC stop command timeout was 1 second, which is shorter than the
time needed for the leaf runner to drain its queue and exit on its own after receiving
`request_stop()`. So workers always timed out on the IPC ack, but the lifecycle manager
was not escalating to OS-level termination — it just gave up.

---

### P-4. `ControlPlaneLeafSinkDispatchAckEvent` not routed to tombstone finalize node

Even after `observe_sink_dispatch_ack` was added to the readiness service,
`ControlPlaneLeafSinkDispatchAckEvent` was not wired as input to
`ControlPlaneLeafTombstoneFinalizeNode` in the leaf plan builder:

```python
# Before fix — in build_leaf_control_plane_system_plan():
ControlPlaneLeafSinkDispatchAckEvent: [
    "system.cp.leaf_source_poll_from_sink_ack",
    # "system.cp.leaf_tombstone_finalize" ← missing
],
```

So even though the node's `consumes` declaration included
`ControlPlaneLeafSinkDispatchAckEvent`, the routing table never delivered ACK events
to it. The ACK path was dead.

---

## Fixes Applied (uncommitted, branch `feature/extract-kernel`)

### Fix P-1 — Stop all spawned workers regardless of `expected_groups`

`ControlPlaneRootStopNode.__call__` now passes `expected_groups=None` to
`_spawned_workers_for_shutdown` and `_root_leaf_ingress_scheduler_cancel_commands`.

When `expected_groups` is `None`, `_spawned_workers_for_shutdown` returns all
workers found in the state event log without group filtering. Every spawned worker —
including `system.observability` — receives a `ControlPlaneLeafStopRequestEvent`
and its ingress scheduler jobs are cancelled.

```python
# After fix:
for group_name, worker_id in _spawned_workers_for_shutdown(
    events=events,
    expected_groups=None,         # ← stop all spawned workers
):
```

Function signatures updated to accept `tuple[str, ...] | None`.

---

### Fix P-2 — Wait for sink dispatch ACK before declaring drain-ready

`ControlPlaneLeafTombstoneFinalizeNode` now has two code paths:

```python
def __call__(self, msg, _ctx):
    payload = msg.payload if isinstance(msg, Envelope) else msg
    if isinstance(payload, ControlPlaneLeafSinkDispatchAckEvent):
        event = self.readiness.observe_sink_dispatch_ack(payload)
    elif isinstance(payload, ControlPlaneLeafBoundaryOutputsEvent):
        if isinstance(payload.source_target, str) and payload.source_target:
            return []   # ← source-driven: wait for sink ACK instead
        event = self.readiness.observe_boundary_outputs(payload)
    else:
        return []
```

For source-driven processing (`source_target` set) — the path that matters for data
plane tombstones in the ring topology — the finalize node suppresses the boundary
outputs event and waits for `ControlPlaneLeafSinkDispatchAckEvent`. ACK arrives only
after the tombstone envelope has been dispatched to the next stage's IPC pipe.

For non-source-driven events (control-plane-originated, `source_target` absent) —
the original `observe_boundary_outputs` path is kept as fallback.

`InMemoryControlPlaneLeafShutdownReadinessService.observe_sink_dispatch_ack()` uses
the same internal `_observe_tombstone` helper as `observe_boundary_outputs`, with the
same deduplication semantics.

---

### Fix P-3 — Proper terminate/kill escalation after graceful timeout

`LocalExecutionWorkerLifecycleService.stop_worker()` now escalates after graceful join:

```
stop_event.set()
process.join(graceful_timeout_seconds)          ← wait for clean exit
if alive: process.terminate()
          process.join(terminate_timeout_seconds)
if alive: process.kill()
          process.join(terminate_timeout_seconds)
if alive: return False
```

`terminate()` sends SIGTERM (Unix) / TerminateProcess (Windows).
`kill()` sends SIGKILL (Unix) — cannot be caught or ignored.

The `terminate_timeout_seconds` parameter is now used at both escalation steps.

---

### Fix P-4 — Route `ControlPlaneLeafSinkDispatchAckEvent` to tombstone finalize node

One line added to `build_leaf_control_plane_system_plan()`:

```python
ControlPlaneLeafSinkDispatchAckEvent: [
    "system.cp.leaf_source_poll_from_sink_ack",
    "system.cp.leaf_tombstone_finalize",        # ← added
],
```

---

## Supporting Changes

**`observability_dispatch.py`** — `DispatchingObservabilityService.on_run_end()` now
drains the dispatch queue synchronously after stopping the dispatch thread. Previously,
observability events queued after the last dispatch thread wake were silently dropped.
`_stop_dispatch_worker()` now returns `bool` (True if thread stopped cleanly) so
`on_run_end` can decide whether a sync drain is safe.

**`runner.py`, `control_plane_service.py`, `runner_execution_service.py`, `boundary_runtime.py`** —
`process_group: str | None` field added to `SyncRunner` and `AsyncRunner`. Leaf runner
receives `process_group` from `__process_group` in the runtime config dict; root runner
uses `"supervisor"`. Used to enrich observability context with `__process_group`,
`__handoff_from`, `__route_hop`, `__parent_span_id`.

---

## Pre-existing Test Failures (not introduced by current changes)

Verified by running baseline (`git stash`) and current branch side-by-side.
All of the following fail identically in both states:

| test | symptom |
|------|---------|
| `test_monitoring_prometheus_export_failure_is_isolated` | `close()` propagates `OSError` from disk write — should swallow it |
| `test_framework_run.py::*` | `RoutingError: No consumers for 'SinkLine'` — `SinkLine` not registered in consumer registry for test scenario |
| `test_inject_ipc_receive_policy_registers_buffer` | `InjectionRegistryError: Missing binding for service<RuntimeDebugBufferService>` |
| `test_inject_ipc_receive_policy_requires_qualifier` | same |
| `test_runtime_boundary_service_contract` | `TypeError: unexpected keyword 'stream_callback'` — signature mismatch in stub |
| `test_builder.py::test_run_with_sync_runner_root_pulse_path_ignores_non_root_inputs` | routing/builder regression |
| `test_builder.py::test_run_with_sync_runner_drops_non_root_inputs_in_root_control_plane_mode` | same |
| `test_builder.py::test_register_discovered_services_registers_routing_service` | same |
| `test_logging_redis_debug.py::*` | Redis debug log sink tests |
| `test_observability_metrics_service::test_worker_queue_telemetry_service_async_fallback_does_not_require_to_thread` | async fallback test |

The `test_real_spawn_ring_pipeline_10_processes_with_observability_10k_messages_e2e`
test fails on the baseline but **passes** with the current uncommitted changes — the
shutdown fixes allow the e2e spawn test to complete cleanly.

---

## Test Coverage for Fixes

All 74 tests covering the changed files pass:

```
tests/stream_kernel/execution/orchestration/control_plane/root/test_control_plane_root_stop_node.py
tests/stream_kernel/execution/orchestration/control_plane/leaf/test_control_plane_leaf_runtime_nodes.py
tests/stream_kernel/execution/orchestration/runtime/test_runner_execution_service.py
tests/stream_kernel/execution/runtime/test_runner_observers.py
tests/stream_kernel/platform/services/runtime/test_control_plane_shutdown_readiness_service.py
tests/stream_kernel/platform/services/runtime/test_worker_lifecycle_service.py
```

New tests added:
- `test_leaf_tombstone_finalize_node_uses_sink_ack_for_source_driven_tombstone`
- `test_leaf_tombstone_finalize_node_waits_for_sink_ack_when_source_target_present`
- `test_leaf_shutdown_readiness_emits_drain_ready_from_sink_ack_tombstone`
- `test_leaf_shutdown_readiness_ignores_non_tombstone_sink_ack`
- `test_worker_lifecycle_stop_terminates_process_after_graceful_timeout`
- `test_worker_lifecycle_stop_kills_process_when_terminate_is_insufficient`
- `test_root_stop_node_emits_leaf_stop_requests_and_requests_runner_stop` (updated: now expects all spawned workers, not just expected_groups)

---

## Relevant Files

| file | role |
|------|------|
| `src/stream_kernel/execution/orchestration/control_plane/root/system_nodes.py:1002` | `ControlPlaneRootStopNode.__call__` — `expected_groups=None` |
| `src/stream_kernel/execution/orchestration/control_plane/root/system_nodes.py:1289` | `_spawned_workers_for_shutdown` — `None` means all workers |
| `src/stream_kernel/execution/orchestration/control_plane/leaf/system_nodes.py:1045` | `ControlPlaneLeafTombstoneFinalizeNode` — ACK-driven drain-ready |
| `src/stream_kernel/execution/orchestration/control_plane/leaf/plan_builder.py:288` | `ControlPlaneLeafSinkDispatchAckEvent` routed to `leaf_tombstone_finalize` |
| `src/stream_kernel/platform/services/runtime/control_plane_shutdown_readiness.py:148` | `InMemoryControlPlaneLeafShutdownReadinessService.observe_sink_dispatch_ack` |
| `src/stream_kernel/platform/services/runtime/lifecycle/worker_service.py:161` | `LocalExecutionWorkerLifecycleService.stop_worker` — terminate/kill escalation |
| `src/stream_kernel/platform/services/observability_dispatch.py:284` | `on_run_end` — sync queue drain after dispatch thread stop |
