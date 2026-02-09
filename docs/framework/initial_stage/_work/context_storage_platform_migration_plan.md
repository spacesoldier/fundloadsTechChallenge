# Context storage migration plan (to framework-native ports)

## Goal
Move context persistence from custom `ContextStore` port to framework-native
port taxonomy, with deterministic default behavior and pluggable adapters.

Target direction:

- use standard `kv` port for context data
- keep default adapter as in-memory `dict` wrapper
- add optional adapters later: `cachetools` then Redis

---

## Why

Current custom `ContextStore` works, but duplicates framework abstractions.
If context is "just keyed state", it should use the same platform rails as
other keyed state interactions.

This reduces special cases and keeps runtime/execution model uniform.

---

## Proposed model

1. Context key
- key: `trace_id` (current behavior preserved)

2. Context value
- value: metadata mapping (`dict[str, object]`) with reserved internal keys

3. Port
- use `kv` binding for context persistence
- no custom context port in final model

4. Adapter lifecycle
- one adapter instance per scenario (same as other injected components)

---

## Migration phases (TDD-first)

### Phase A: Contract freeze
- Add tests that lock current runtime behavior:
  - deterministic key strategy (`run_id:index`)
  - initial metadata contents
  - retrieval before node call
  - no context leakage across messages/scenarios

### Phase B: KV-backed context facade
- Introduce runtime-facing context facade backed by `kv` adapter.
- One-pass migration: no `ContextStore` shim layer.
- Add tests proving strict equivalence with previous behavior.

### Phase C: Remove custom ContextStore usage from Runner
- Update `SyncRunner` to depend on `ContextService` facade backed by `kv` (not direct store adapter).
- Keep method semantics (`get/set/delete`) inside service boundary, not runner boundary.
- Add integration tests:
  - `Runner ↔ RoutingPort ↔ WorkQueue ↔ KV-context`

### Phase D: Default in-memory adapter
- Implement/lock default in-memory `kv` adapter as thin `dict` wrapper.
- Add tests for:
  - idempotent put/overwrite
  - delete semantics
  - scenario-local isolation
- Status:
  - `runtime.platform.kv.backend` is normalized by validator and defaults to `memory`.
  - Runtime auto-provisions `kv<KVStore>` binding for `memory` backend when no explicit `kv` binding exists.

### Phase E: Optional adapter pack
- `cachetools` adapter (TTL/LRU config)
- Redis adapter
- Conformance tests must pass for all adapters.

### Phase F: Cleanup
- Remove transitional `ContextStore` port/classes.
- Update docs and runtime builder to framework-native context path only.

---

## Test checklist

1. Same trace_id -> same context record path.
2. Missing context -> empty metadata view.
3. Reserved keys preserved (`__trace_id`, `__run_id`, `__scenario_id`).
4. Service/full-context node sees full context; regular node sees metadata slice.
5. No behavioral drift in baseline end-to-end output after migration.

---

## Risks

1. Hidden coupling to `ContextStore` class in tests/runtime.
2. Mutation behavior differences between adapters (`dict` vs redis serialization).
3. Memory retention if TTL/cleanup policy is not defined for long runs.

Mitigation:

- freeze behavior with integration tests before swapping implementation
- add adapter conformance suite
- define cleanup policy explicitly per adapter type
