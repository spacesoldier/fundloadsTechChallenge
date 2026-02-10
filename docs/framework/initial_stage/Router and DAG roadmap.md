# Router + DAG roadmap (consumes/emits)

This plan tracks the move from fixed `runtime.pipeline` ordering to
**type-driven routing** with a **DAG analysis layer**.

---

## Goals

- Replace manual pipeline order with **runtime routing** based on `consumes/emits`.
- Keep a **DAG graph** for analytics, cycle detection, and trace inspection.
- Make routing deterministic and testable.
- Migrate execution away from the legacy `kernel.runner` to the new execution layer. ✅
- Treat **adapters as source/sink nodes** so entry/exit points are explicit in the graph.

---

## Decisions (locked)

- Use **consumes/emits** terminology in docs and code.
- `consumes` must be **non-empty** for every non‑source node.
- Routing is **dynamic**; DAG is **analytic** (not a strict execution plan).
- Adapter config does not use per-project factory paths.
- Adapter config does not declare model/type class strings.
- Adapter selection in YAML is name-based (`adapters.<name>`), not `kind`-based.
- Stable port types: `stream`, `kv_stream`, `kv`, `request`, `response`.

---

## Phase 1 — Docs + contracts

- [x] Update docs to use **consumes/emits** everywhere.
- [x] Document routing vs DAG: routing is runtime, DAG is analytic.

---

## Phase 2 — DAG analysis (static)

### Tests
- [x] Detect cycles in the consumes/emits graph.
- [x] Allow multiple providers/consumers per type.
- [x] Missing provider for a consumed type → error.
- [x] Deterministic ordering for unrelated nodes (discovery order).

### Implementation
- [x] Build a graph from emits → consumes edges.
- [x] Add cycle detection utility.
- [x] Expose graph summary (nodes/edges, fan-in/out).

---

## Phase 3 — Router runtime (dynamic)

### Tests
- [x] Route messages to all consumers of their type.
- [x] Preserve stable delivery order (discovery order).
- [x] Envelope routing: `target` overrides type-based fan-out.
- [x] Multi-output routing (per-output target/topic).
- [x] Self-loop protection for default fan-out (`source` excluded unless explicitly targeted).
- [ ] Enforce max-hops / retry limit for cycles.
- [x] Behavior when no consumers exist (error vs drop) — decided and tested.
- [x] ConsumerRegistry port contract and adapter tests.

### Implementation
- [x] Replace step-by-step pipeline with router dispatch loop.
- [ ] Remove default output sink; require explicit sink node.
- [x] Ensure tracing covers routing hops and fan-out (sync runtime baseline).
- [x] Implement `Envelope` with `target` routing (see Routing semantics).
- [x] Add WorkQueue + KV context storage (see Execution runtime and routing integration).
- [x] Introduce ConsumerRegistry + RoutingPort (Runner depends on RoutingPort).
- [ ] Source/sink adapter nodes: remove hardcoded `input_source`/`output_sink` path.

---

## Phase 4 — Context store port

### Tests
- [x] In-memory context store (default).
- [x] Context persistence migration to framework-native `kv` port contract.
- [ ] Adapter conformance tests for context backends (`dict`, `cachetools`, `redis`).

### Implementation
- [x] Port + in-memory adapter for context storage.
- [x] Hook into router runtime.
- [x] Replace custom `ContextStore` dependency in runner with framework-native `kv` storage.
- [ ] Keep runtime behavior identical during migration (trace_id keying + metadata view rules).

---

## Phase 5 — Config and migration

- [x] Make `runtime.pipeline` optional/deprecated.
- [x] Remove `runtime.pipeline` from runtime execution path (rejected at runtime; no legacy fallback).
- [x] Update newgen configs (baseline/experiment) to remove pipeline.
- [ ] Update docs + examples.
- [ ] Add config for **source/sink adapters** as nodes (entry/exit in DAG).
- [x] Replace runtime next-step shim; execution order now comes from DAG plan.
- [x] Remove adapter `factory` from runtime config path (name + settings + binds only).
- [x] Build adapter registry from discovery output (same principle as node discovery).
- [x] Enforce framework-supported adapter names in validator/runtime.
- [x] Remove model/type strings from adapter YAML contracts.
- [x] Introduce service-layer migration (`inject.service(...)`) to retire project-level domain ports.
- [x] Add explicit `@service` discovery marker and lifecycle checks.

## Phase 6 — Platform-ready validation for demo project

- [ ] Add static contract rule: if a node has `consumes=[T]` and `emits=[T]`,
      require either:
      - stage-specific token split (`T_in` -> `T_out`), or
      - explicit output target contract.
- [ ] Expose this rule in dev tooling (pyright/mypy-friendly diagnostics + CI check).
- [ ] Add a preflight validation command for graph+routing invariants before run.
- [ ] Run full demo project on framework-only runtime without legacy sequencing hooks.

## Phase 7 — Network interface track (HTTP/WebSocket/GraphQL)

- [ ] Add config-level declarations for network interfaces in runtime section.
- [ ] Keep protocol adapters platform-owned and discovery-driven (no runtime hardcode).
- [ ] Map transport semantics to stable ports:
      - HTTP req/res -> `request`/`response`
      - WS and server stream -> `stream`
      - keyed protocol streams -> `kv_stream`
- [ ] Integrate ingress/egress boundaries into tracing/telemetry/monitoring.
- [ ] Add adapter conformance tests for initial network stack (FastAPI HTTP, WS, GraphQL baseline).

### Runtime note

- Runtime bootstrap starts via token routing (`RoutingPort.route([payload])`).
- Scenario build order is derived from DAG execution plan, not discovery order.
- Ambiguous token flows are handled by routing contracts/targets, not by next-step shims.
- Runtime entrypoint is framework-first (`run` / `run_with_config`) with artifact assembly through execution builder APIs.

---

## Open questions (to resolve)

- What is the retry/max-hop policy for cycles?
- Message identity for context storage: trace_id vs msg.id?
- For nodes with `consumes=[T]` and `emits=[T]`:
  - migrate to stage-specific tokens, or
  - enforce explicit target contracts and loop budgets.

---

## Phase 0 — Runner migration (legacy → execution)

### Goals

- Deprecate `stream_kernel.kernel.runner` and move execution to
  `stream_kernel.execution.SyncRunner`.
- Route via `RoutingPort` + `WorkQueue` + KV context storage instead of
  direct step‑by‑step pipeline calls.

### Steps

1. **Audit dependencies** ✅
   - Imports of `stream_kernel.kernel.runner` identified and cleaned up.
   - Legacy runner tests identified and migrated/removed.
2. **Introduce execution interface**
   - `RunnerPort` interface (done).
   - `SyncRunner` implementation (done).
3. **Bridge execution to routing**
   - Use `WorkQueue`, `RoutingPort`, and KV-backed context storage.
   - Adapters remain **payload-only**; runner wraps outputs into `Envelope`.
4. **Migrate runtime wiring** ✅
   - Legacy runner usage in `app/runtime.py` replaced by execution runtime path.
5. **Test migration** ✅
   - Integration tests for runner+router+context are in place.
   - Legacy runner tests removed/replaced.
6. **Deprecate legacy runner** ✅
   - `kernel.runner` removed from code.
