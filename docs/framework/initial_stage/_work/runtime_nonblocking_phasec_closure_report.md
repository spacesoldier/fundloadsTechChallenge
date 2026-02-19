# Runtime non-blocking observability — Phase C closure report

## Date: 2026-02-15

## Summary

Phase C of `runtime_nonblocking_and_graceful_stop_audit_plan.md` is complete.
Test suite: **850 passed, 0 failed** after Phase C (was 846 before).

---

## What changed

### `TracingObserver` — routing-native emission

`TracingObserver` now accepts two optional constructor parameters:

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `trace_queue` | `object \| None` | `None` | Work queue to route trace records through. When set, records are pushed as `Envelope` messages instead of being emitted directly to the sink. |
| `trace_sink_node_name` | `str` | `"system.obs.trace_sink"` | Target node name for the routed envelope. |

### Routing logic — `_emit_record(record)`

A private helper centralises the dispatch decision:

```
if trace_queue is set:
    trace_queue.push(Envelope(payload=record, target=trace_sink_node_name))
else:
    sink.emit(record)   ← backwards-compatible fallback
```

`after_node()` and `on_node_error()` both delegate to `_emit_record()`.

### `on_run_end()` — conditional flush

```
if trace_queue is set:
    return   ← sink lifecycle owned by TraceSinkNode; runner drain handles flush/close
else:
    sink.flush(); sink.close()   ← direct-emit path unchanged
```

### Backwards compatibility

All pre-Phase-C tests continue to pass because:
- `trace_queue` defaults to `None` → `_emit_record()` falls back to `sink.emit()`
- `on_run_end()` still calls `flush()/close()` on the direct-emit path

### `TraceSinkNode` — processes routed records

`TraceSinkNode.__call__` (defined in Phase B) already handles the envelope correctly:

```python
record = msg.payload if isinstance(msg, Envelope) else msg
emit = getattr(self.sink, "emit", None)
if callable(emit):
    emit(record)
```

No changes to `TraceSinkNode` were needed for Phase C.

---

## Hot-path impact

| Scenario | Before Phase C | After Phase C |
|----------|---------------|---------------|
| `after_node()` with OTLP/file sink | Blocks for full I/O (network/disk) | Returns after in-memory queue push (microseconds) |
| `after_node()` without `trace_queue` | Blocks (same as before) | Unchanged — backwards compat |
| Sink flush/close | Called in `on_run_end()` | Deferred to `TraceSinkNode` teardown via runner drain |

---

## New tests (4)

| Test | What it verifies |
|------|-----------------|
| `test_trace_after_node_routes_to_queue_not_sink` | `sink.emit()` not called; queue has `Envelope` targeting `system.obs.trace_sink` with correct `TraceRecord` payload |
| `test_trace_on_node_error_routes_to_queue_not_sink` | Error records also routed; `status == "error"` preserved |
| `test_trace_on_run_end_skips_flush_when_queue_routed` | `sink.flush()/close()` not called when `trace_queue` is set |
| `test_trace_sink_node_processes_trace_record_from_queue` | Full round-trip: observer pushes to queue → `TraceSinkNode` processes envelope → `sink.emit()` called once with correct record |

---

## Files modified

| File | Change |
|------|--------|
| `src/stream_kernel/observability/observers/tracing.py` | Added `trace_queue`, `trace_sink_node_name` params; added `_emit_record()` helper; updated `after_node()`, `on_node_error()`, `on_run_end()` |
| `tests/stream_kernel/observability/test_tracing_observer.py` | 4 new Phase C tests |

---

## Pending

- Phase D — SyncRunner stop signal + multiprocess output-closed ack
- Phase E — retire `_run_async_blocking` from hot path
- Phase F — docs sync
