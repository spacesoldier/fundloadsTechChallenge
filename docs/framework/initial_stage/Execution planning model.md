# Execution planning model (queues + pools)

This document describes **how execution is scheduled** when we move from a single
pipeline runner to **multiple queues and pools** (sync/async).

It complements:

- [Execution runtime and routing integration](Execution%20runtime%20and%20routing%20integration.md)
- [Routing semantics](Routing%20semantics.md)

---

## 1) Goal

We want:

- predictable routing (Router)
- flexible execution (Runner variants)
- ability to scale **CPU‑bound** and **IO‑bound** work separately

---

## 2) Queue model

### 2.1 Per‑node queues (preferred)

Each node has its own queue:

```
queue[node_id] -> Node
```

**Runner acts as a scheduler**:

- polls queues in **round‑robin**
- or uses priority/weights

### 2.2 Pooled queues (fallback)

Queues grouped by pool:

```
queue[cpu] -> CPU nodes
queue[io]  -> IO nodes
```

Each runner instance works on its pool only.

---

## 3) How do we choose sync vs async pool?

We **infer** the pool from adapter capabilities by default.
Node annotations should remain clean; explicit overrides are optional and future‑scoped.

### 3.1 Default rule (inferred)

- If a node depends on any **async‑capable adapter**, it is assigned to **async** pool.
- Otherwise it stays in **sync** pool.

### 3.2 Optional override (future)

We may add an explicit override later (e.g., in config), but it is **not required**
for the initial model.

---

## 3.3 Async adapters without `await` in nodes

Goal: keep **node code synchronous** while still allowing async adapters.

### Model

- Adapters may be **async-capable** (marker in the framework).
- Injection registry **records** whether a node depends on any async adapter.
- Execution planner assigns such nodes to the **async pool**.

### Behavior

- Node code remains sync (`def __call__`).
- Async adapters are wrapped by a **sync facade**:
  - internally executes on an event loop or IO pool
  - returns results synchronously to the node

### Why this matters

- avoids `await` bleeding into business code
- preserves deterministic flow (routing/DAG still governs ordering)
- allows multiple runner implementations (sync/async)

### Open point (for adapter design)

Async capability is detected via adapter metadata/registration.
The registry is responsible for exposing this signal to the planner.

---
## 4) Are adapters nodes?

Adapters are **integration components**.  
They may be **exposed as nodes** if they:

- **produce** messages (sources)
- **consume** messages (sinks)

So:

- Adapter ≠ node by default
- Adapter can be **wrapped** or **registered** as a node when it is part
  of the execution graph.

This keeps the domain clean while enabling external IO inside the DAG.

---

## 5) Scheduling policy (sync runner)

### 5.1 Round‑robin

- iterate node queues in stable order
- pop one message per queue

### 5.2 Weighted

- nodes may have `weight`
- higher weight → more messages per cycle

---

## 6) Test cases (TDD)

### 6.1 Per‑node queue scheduling

- queues: A, B, C (all non‑empty)
- round‑robin delivers in A→B→C order

### 6.2 Empty queues are skipped

- B empty, A and C non‑empty
- round‑robin yields A→C→A→C…

### 6.3 Pool separation

- CPU runner sees only CPU queues
- IO runner sees only IO queues

### 6.4 Sync node stays in sync pool

- Node uses only sync adapters.
- Planner assigns node to **sync** pool.

### 6.5 Async adapter drives async pool

- Node depends on an adapter marked `async-capable`.
- Registry records async dependency.
- Planner assigns node to **async** pool.

### 6.6 Mixed dependencies (sync + async)

- Node depends on both sync and async adapters.
- Planner prefers **async** pool (IO-safe default).

### 6.7 Sync facade hides async implementation

- Node calls adapter method synchronously.
- Adapter executes on event loop internally.
- Node does **not** use `await`.

### 6.8 Pool separation keeps ordering by routing

- Nodes are split between pools.
- Routing still guarantees delivery order per message flow.

---

## 7) Future extensions

- dynamic pool assignment (load‑based)
- backpressure + rate limits per pool
- GPU queues with separate runner
