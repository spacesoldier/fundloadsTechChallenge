# Runtime non-blocking observability — Phase E closure report

## Date: 2026-02-16

## Summary

Phase E of `runtime_nonblocking_and_graceful_stop_audit_plan.md` is complete.
Test suite: **858 passed, 0 failed** after Phase E (was 854 before).

---

## What changed

### `OTelOtlpTraceSink` — native async emit path

Three new async methods added to `OTelOtlpTraceSink`:

| Method | Purpose |
|--------|---------|
| `emit_async(record)` | Hot-path async emit; mirrors `emit()` buffer logic but flushes via `_flush_batch_async()`. `_run_async_blocking()` is NOT called here. |
| `_flush_batch_async()` | Async batch flush. httpx-async → `_post_http_httpx_async_native`; aiohttp → `_post_http_aiohttp_native`; sync backends → `asyncio.to_thread(self._post_http, batch)`. |
| `_post_http_httpx_async_native(body)` | `await client.post(...)` directly on the running loop. No new event loop or thread. |
| `_post_http_aiohttp_native(body)` | `async with session.post(...) as response:` directly on the running loop. No new event loop or thread. |

The existing `_post_http_httpx_async()` and `_post_http_aiohttp()` (which call `_run_async_blocking`) are **retained** for the sync shutdown path (`close()` / `flush()`).

### `TraceSinkNode` — emit_async dispatch

`TraceSinkNode.__call__` now checks for `emit_async` before falling back to `emit`:

```
if sink has emit_async:
    return self._async_emit(emit_async_fn, record)  ← returns coroutine
else:
    sink.emit(record); return []                      ← sync path unchanged
```

`_async_emit(fn, record)` is an `async def` helper that awaits `fn(record)` and returns `[]`, satisfying `_coerce_node_outputs`'s `list(resolved)` expectation.

When `TraceSinkNode` is registered in `AsyncRunner` (via `plan_pools()` detecting async capability), the returned coroutine is awaited by `AsyncRunner._coerce_node_outputs` via `_maybe_await`. No extra thread is spawned.

### `_run_async_blocking` — hot-path removal

| Location | Before Phase E | After Phase E |
|----------|---------------|---------------|
| `OTelOtlpTraceSink.emit()` — httpx-async | `_post_http_httpx_async()` → `_run_async_blocking(_send())` | Only called for sync path |
| `OTelOtlpTraceSink.emit()` — aiohttp | `_post_http_aiohttp()` → `_run_async_blocking(_send())` | Only called for sync path |
| `OTelOtlpTraceSink.emit_async()` — httpx-async | N/A | `await _post_http_httpx_async_native(body)` |
| `OTelOtlpTraceSink.emit_async()` — aiohttp | N/A | `await _post_http_aiohttp_native(body)` |
| `close()` / `flush()` shutdown path | `_run_async_blocking` | Retained (one-time blocking is acceptable at shutdown) |

---

## Hot-path improvement

| Scenario | Before Phase E | After Phase E |
|----------|---------------|---------------|
| AsyncRunner + async trace sink (httpx-async) | `emit()` called → `_run_async_blocking` → new thread + new event loop per batch | `emit_async()` awaited directly on runner loop; 0 extra threads |
| AsyncRunner + async trace sink (aiohttp) | Same as above | Same improvement |
| SyncRunner or sync sink | `emit()` → `_flush_batch()` (unchanged) | Unchanged |
| Shutdown (`close()`/`flush()`) | `_run_async_blocking` | Retained (correct) |

---

## New tests (4)

| Test | What it verifies |
|------|-----------------|
| `test_otlp_sink_has_emit_async_method` | `OTelOtlpTraceSink` exposes `emit_async` as a coroutine function |
| `test_trace_sink_node_returns_coroutine_for_async_sink` | `TraceSinkNode.__call__` returns a coroutine (not `[]`) when sink has `emit_async` |
| `test_trace_sink_node_dispatches_to_emit_async_not_emit` | After awaiting the coroutine: `emit_async` called once with the record; `emit` NOT called |
| `test_async_runner_awaits_emit_async_on_runner_loop` | Full AsyncRunner integration: `emit_async` runs on the runner's event loop in the main thread; `emit` not called; no daemon thread spawned |

---

## Files modified

| File | Change |
|------|--------|
| `src/stream_kernel/adapters/trace_sinks.py` | Added `emit_async`, `_flush_batch_async`, `_post_http_httpx_async_native`, `_post_http_aiohttp_native` to `OTelOtlpTraceSink` |
| `src/stream_kernel/execution/orchestration/observability_system_nodes.py` | `TraceSinkNode.__call__` checks `emit_async` and dispatches; added `_async_emit` coroutine helper |
| `tests/stream_kernel/execution/runtime/test_phase_e_async_emit.py` | New Phase E test file (4 tests) |

---

## Pending

- Phase F — docs sync
