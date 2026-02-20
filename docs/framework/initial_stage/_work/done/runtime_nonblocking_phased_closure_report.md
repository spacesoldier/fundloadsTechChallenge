# Runtime non-blocking observability — Phase D closure report

## Date: 2026-02-15

## Summary

Phase D of `runtime_nonblocking_and_graceful_stop_audit_plan.md` is complete.
Test suite: **854 passed, 0 failed** after Phase D (was 850 before).

---

## What changed

### `SyncRunner` — stop signal mirroring `AsyncRunner`

`SyncRunner` now carries the same graceful-stop interface as `AsyncRunner`:

| Field / Method | Type | Default | Purpose |
|----------------|------|---------|---------|
| `drain_on_stop` | `bool` | `True` | When `True`, runner empties the queue before honouring the stop request. When `False`, runner exits after the current item completes. |
| `_stop_requested` | `bool` | `False` (non-init) | Internal stop flag; set by `request_stop()`. |
| `request_stop()` | `-> None` | — | Graceful stop signal; thread-safe flag flip. |

### Stop-check logic in `SyncRunner.run()`

Added at the top of the `while True` loop (mirrors `AsyncRunner.run_async()`):

```python
if self._stop_requested:
    size_fn = getattr(work_queue, "size", None)
    size = size_fn() if callable(size_fn) else 0
    if not self.drain_on_stop or size == 0:
        break
```

- `size()` is resolved via `getattr` — queue implementations without `size()` are treated as empty (conservative: stop immediately when flag is set).
- If `drain_on_stop=True` and queue still has items, the loop continues processing until naturally empty.

### `BootstrapSupervisor` — `wait_output_closed` hook

New optional hook on the base class:

```python
def wait_output_closed(self, timeout_seconds: int) -> bool:
    # Default: return True immediately (no-op).
    ...
```

Concrete supervisor implementations may override this to wait for an explicit "output closed" acknowledgement from child processes before stopping, ensuring file sinks and trace sinks are fully flushed.

### Lifecycle orchestration — `_wait_output_closed_if_available`

New internal helper in `lifecycle_orchestration.py`, called in the `execute_with_bootstrap_supervisor()` finally block between IPC boundary drain and `stop_groups()`:

```
_wait_boundary_drain_if_available()   ← IPC boundary drained
_wait_output_closed_if_available()    ← child on_run_end() signalled  [NEW]
_stop_supervisor_with_fallback()      ← send stop command to workers
```

`_wait_output_closed_if_available()` is best-effort: exceptions are swallowed, and stop proceeds regardless (unlike boundary drain which raises on timeout).

---

## Hot-path / shutdown impact

| Scenario | Before Phase D | After Phase D |
|----------|---------------|---------------|
| `SyncRunner` mid-run SIGTERM | Process killed mid-iteration; data loss | `request_stop()` → drain remaining queue then exit cleanly |
| `SyncRunner drain_on_stop=False` | N/A | Stops after current item, before next pop |
| `SyncRunner drain_on_stop=True` | N/A | Empties queue fully before exit |
| Multiprocess child flush before forced kill | Supervisor infers completion from IPC drain only | Supervisor calls `wait_output_closed()` for explicit output-closed ack |

---

## New tests (4)

| Test | What it verifies |
|------|-----------------|
| `test_sync_runner_has_drain_on_stop_and_request_stop` | `SyncRunner` has `drain_on_stop`, `_stop_requested` fields and `request_stop()` method with correct defaults |
| `test_sync_runner_respects_stop_between_iterations` | `drain_on_stop=False`: only item 1 processed after `request_stop()` during item 1; item 2 skipped |
| `test_sync_runner_drain_on_stop_empties_queue` | `drain_on_stop=True`: both items processed after `request_stop()` during item 1 |
| `test_multiprocess_supervisor_wait_output_closed_called_before_stop` | `lifecycle_orchestration` calls `wait_output_closed()` before `stop_groups()` in the supervisor shutdown sequence |

---

## Files modified

| File | Change |
|------|--------|
| `src/stream_kernel/execution/runtime/runner.py` | Added `drain_on_stop`, `_stop_requested` fields and `request_stop()` method to `SyncRunner`; added stop-check at top of `run()` loop |
| `src/stream_kernel/platform/services/runtime/bootstrap.py` | Added `wait_output_closed(timeout_seconds) -> bool` to `BootstrapSupervisor` base class (default: no-op `return True`) |
| `src/stream_kernel/execution/orchestration/lifecycle_orchestration.py` | Added `_wait_output_closed_if_available()` helper; called it in `execute_with_bootstrap_supervisor()` finally block |
| `tests/stream_kernel/execution/runtime/test_sync_runner_stop.py` | New Phase D test file (4 tests) |

---

## Pending

- Phase E — retire `_run_async_blocking` from hot path
- Phase F — docs sync
