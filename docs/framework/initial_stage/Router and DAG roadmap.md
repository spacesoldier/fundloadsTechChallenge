# Router + DAG roadmap (consumes/emits)

This plan tracks the move from fixed `runtime.pipeline` ordering to
**type-driven routing** with a **DAG analysis layer**.

---

## Goals

- Replace manual pipeline order with **runtime routing** based on `consumes/emits`.
- Keep a **DAG graph** for analytics, cycle detection, and trace inspection.
- Make routing deterministic and testable.
- Migrate execution away from the legacy `kernel.runner` to the new execution layer.
- Treat **adapters as source/sink nodes** so entry/exit points are explicit in the graph.

---

## Decisions (locked)

- Use **consumes/emits** terminology in docs and code.
- `consumes` must be **non-empty** for every non‑source node.
- Routing is **dynamic**; DAG is **analytic** (not a strict execution plan).

---

## Phase 1 — Docs + contracts

- [x] Update docs to use **consumes/emits** everywhere.
- [x] Document routing vs DAG: routing is runtime, DAG is analytic.

---

## Phase 2 — DAG analysis (static)

### Tests
- [ ] Detect cycles in the consumes/emits graph.
- [ ] Allow multiple providers/consumers per type.
- [ ] Missing provider for a consumed type → error.
- [ ] Deterministic ordering for unrelated nodes (discovery order).

### Implementation
- [ ] Build a graph from emits → consumes edges.
- [ ] Add cycle detection utility.
- [ ] Expose graph summary (nodes/edges, fan-in/out).

---

## Phase 3 — Router runtime (dynamic)

### Tests
- [ ] Route messages to all consumers of their type.
- [ ] Preserve stable delivery order (discovery order).
- [ ] Envelope routing: `target` overrides type-based fan-out.
- [ ] Multi-output routing (per-output target/topic).
- [ ] Enforce max-hops / retry limit for cycles.
- [ ] Behavior when no consumers exist (error vs drop) — decide.
- [ ] ConsumerRegistry port contract and adapter tests.

### Implementation
- [ ] Replace step-by-step pipeline with router dispatch loop.
- [ ] Remove default output sink; require explicit sink node.
- [ ] Ensure tracing covers routing hops and fan-out.
- [ ] Implement `Envelope` with `target` routing (see Routing semantics).
- [ ] Add WorkQueue + ContextStore ports (see Execution runtime and routing integration).
- [ ] Introduce ConsumerRegistry + RoutingPort (Runner depends on RoutingPort).
- [ ] Source/sink adapter nodes: remove hardcoded `input_source`/`output_sink` path.

---

## Phase 4 — Context store port

### Tests
- [ ] In-memory context store (default).
- [ ] Port shape for external store (Redis adapter later).

### Implementation
- [ ] Port + adapter for context storage.
- [ ] Hook into router runtime.

---

## Phase 5 — Config and migration

- [ ] Make `runtime.pipeline` optional/deprecated.
- [ ] Update newgen configs (baseline/experiment) to remove pipeline.
- [ ] Update docs + examples.
- [ ] Add config for **source/sink adapters** as nodes (entry/exit in DAG).

---

## Open questions (to resolve)

- What is the default behavior when no consumer exists?
- What is the retry/max-hop policy for cycles?
- Message identity for context storage: trace_id vs msg.id?

---

## Phase 0 — Runner migration (legacy → execution)

### Goals

- Deprecate `stream_kernel.kernel.runner` and move execution to
  `stream_kernel.execution.SyncRunner`.
- Route via `RoutingPort` + `WorkQueue` + `ContextStore` instead of
  direct step‑by‑step pipeline calls.

### Steps

1. **Audit dependencies**
   - Identify all imports of `stream_kernel.kernel.runner`.
   - List tests that rely on the legacy runner.
2. **Introduce execution interface**
   - `RunnerPort` interface (done).
   - `SyncRunner` implementation (done).
3. **Bridge execution to routing**
   - Use `WorkQueue`, `RoutingPort`, `ContextStore`.
   - Adapters remain **payload-only**; runner wraps outputs into `Envelope`.
4. **Migrate runtime wiring**
   - Replace legacy runner usage in `app/runtime.py`.
   - Provide a compatibility shim if needed.
5. **Test migration**
   - New integration tests for runner+router+context.
   - Remove or rewrite legacy runner tests.
6. **Deprecate legacy runner**
   - Mark docs/specs as legacy.
   - Remove `kernel.runner` after migration passes.
