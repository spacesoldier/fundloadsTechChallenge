# Runtime non-blocking observability — Phase F closure report

## Date: 2026-02-16

## Summary

Phase F (docs sync and sign-off) of `runtime_nonblocking_and_graceful_stop_audit_plan.md` is
complete. All three target documents are updated to reflect the routing-native trace emission
model, the graceful stop interface, and `TraceSinkPort` as a named platform port.

This completes the full audit plan (Phases A–F).

---

## Documents updated

### `Tracing runtime.md`

**Section 5 — Runtime wiring**

- Added step 6 describing the routing-native trace emission path:
  `TracingObserver.after_node()` enqueues `TraceRecord` into the work queue;
  runner processes `system.obs.trace_sink` on the next iteration, outside the
  business hot path.
- Added implementation references: `TraceSinkPort` in `contracts.py`, system nodes
  in `observability_system_nodes.py`.

**New Section 9 — Non-blocking trace emission (routing-native path)**

Comprehensive reference section covering:

- Before/after model: inline `sink.emit()` → work queue enqueue.
- `TraceSinkPort` protocol declaration and all adapter factory `execution_mode` values
  (table).
- Async emit path: `emit_async()`, `_flush_batch_async()`, dispatch to native async
  HTTP clients or `asyncio.to_thread()` for sync backends.
- `SyncRunner` graceful stop fields (`drain_on_stop`, `_stop_requested`,
  `request_stop()`).
- Additional test coverage pointers (Phases D–E).

---

### `Execution runtime and routing integration.md`

**Section 6.1 — SyncRunner (baseline)**

Added: graceful stop note — `request_stop()` / `drain_on_stop=True` empties queue
before exit, including pending `TraceRecord` envelopes.

**Section 6.4 — Runner interface (contract)**

Extended:

- Added `request_stop() -> None` and `drain_on_stop: bool` to the shared runner
  interface.
- Added explicit statement: **runner does not own sink lifecycle**. Trace/log sink
  teardown is the responsibility of `system.obs.*` sink nodes; runner never calls
  `sink.flush()` or `sink.close()` directly.

**Section 7.1 — Execution-level tracing boundary**

Added: routing-native observer note explaining that `TracingObserver.after_node()` enqueues
rather than emits inline, and the recursion guard (`excluded_node_names`).

---

### `Ports and adapters model.md`

**Section 2.3 — Observability adapters are platform-owned**

Added new subsection **2.3.1 TraceSinkPort (trace platform port)**:

- Protocol declaration in `contracts.py` (emit / flush / close).
- All trace adapter factories annotate with `@adapter(execution_mode=..., binds=[("stream", TraceSinkPort)])`.
- `execution_mode` semantics table and `plan_pools()` / `AsyncRunner` interaction.
- Statement: `TraceSinkPort` is the only platform port for trace record emission.

---

## Audit plan — final status

| Phase | Description | Status |
|-------|-------------|--------|
| A | TraceSinkPort as platform port | ✓ complete |
| B | @node/@service rails for observability | ✓ complete |
| C | Routing-native trace emission | ✓ complete |
| D | SyncRunner stop signal + multiprocess output ack | ✓ complete |
| E | Retire `_run_async_blocking` from hot path | ✓ complete |
| F | Docs sync and sign-off | ✓ complete |

---

## Files modified

| File | Change |
|------|--------|
| `docs/framework/initial_stage/Tracing runtime.md` | Section 5 extended; Section 9 added |
| `docs/framework/initial_stage/Execution runtime and routing integration.md` | Sections 6.1, 6.4, 7.1 updated |
| `docs/framework/initial_stage/Ports and adapters model.md` | Section 2.3.1 added |
| `docs/framework/initial_stage/_work/runtime_nonblocking_and_graceful_stop_audit_plan.md` | Phase F marked [x] |
