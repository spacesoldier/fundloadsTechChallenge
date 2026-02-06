# Routing semantics (Envelope + targets)

This document defines **runtime routing** rules (not DAG analysis).
It complements:

- [DAG construction](DAG%20construction.md)
- [Router + DAG roadmap](Router%20and%20DAG%20roadmap.md)

---

## 1) Base model

**Default rule:** if a message has no explicit target, it is routed by
`consumes/emits` (type-driven fan-out).

**Envelope:** all runtime messages are wrapped in an `Envelope`:

- `payload` — the actual object
- `trace_id` — context key used by ContextStore (optional but recommended)
- `target` — optional explicit destination (node name or list of node names)
- `topic` / `channel` — optional, future extension
- `trace` / metadata — optional

The envelope provides a clean routing hook without changing payload types.

---

## 2) Targeted routing

If `Envelope.target` is set:

- the router **delivers only** to the target node(s)
- normal `consumes/emits` fan-out is **skipped**

This supports explicit chains like “Node A → Node B → Node C” even when
nodes are reused at multiple stages (e.g., duplicate ksampler pattern).

---

## 3) Multiple outputs from one node

A node may emit **multiple outputs**. To allow **mixed routing** per output:

- each output can carry its **own** `target` and/or `topic`
- outputs are routed **independently**

We support these patterns:

### 3.1 Explicit output list (recommended)

The node returns a list of outputs:

- `Envelope(payload=..., target=...)`
- `Envelope(payload=..., topic=...)`
- `Envelope(payload=...)` (fan-out by type)

This avoids ambiguity when different payloads need different routing.
If a node produces **multiple types**, this explicit form is preferred.

### 3.2 Positional outputs (optional)

The node returns a list where **position** corresponds to a logical output
slot (e.g., output 0 → default routing, output 1 → topic, output 2 → target).

This is compact but less explicit; use only if a node has a stable contract.

### 3.3 Helper API (future)

Introduce a small helper like `node.emit(...)` (Node-RED style) to build
output envelopes without boilerplate.

---

## 4) Multiple payload types

If a node may emit **different payload types**, routing rules are:

- If output is **explicitly wrapped** (`Envelope`), the router follows it.
- If output is **bare**, the router treats it as a normal message
  and routes by type (`consumes/emits`).

Recommendation: when a node can emit more than one type, wrap each output
explicitly so routing intent is unambiguous.

---

## 5) Multiple sources of the same type

When **multiple sources** emit the same model type:

- routing behaves like **fan‑in** (all sources feed the same token channel)
- ordering is **not** guaranteed unless the scheduler defines one
- fairness policy must be explicit (round‑robin, priority, “as available”)

If deterministic ordering is required, use a **single source** or enforce
ordering in a dedicated aggregation node.

---

## 6) Test cases (TDD)

### 6.1 Default fan‑out by type

**Setup:**
1. Router consumer map: `X -> [B, C]` (discovery order).
2. Input message: `X("x")` (bare payload, no envelope target).

**Expected:**
- Deliveries are `[("B", X("x")), ("C", X("x"))]`.
- No additional routing side effects.

### 6.2 Target overrides type routing

**Setup:**
1. Router consumer map: `X -> [B, C]`.
2. Input message: `Envelope(payload=X("x"), target="C")`.

**Expected:**
- Deliveries are `[("C", X("x"))]` only.
- `B` does not receive the payload even though it consumes `X`.

### 6.3 Multiple targets

**Setup:**
1. Router consumer map: `X -> [B, C]`.
2. Input message: `Envelope(payload=X("x"), target=["B","C"])`.

**Expected:**
- Deliveries are `[("B", X("x")), ("C", X("x"))]`.
- Order follows the target list.

### 6.4 Mixed outputs (explicit envelopes)

**Setup:**
1. Router consumer map: `X -> [B, C]`.
2. Outputs:
   - `Envelope(payload=X("t"), target="B")`
   - `Envelope(payload=X("f"))`

**Expected:**
- Deliveries are `[("B", X("t")), ("B", X("f")), ("C", X("f"))]`.
- Targeted output is delivered only to `B`.

### 6.5 Mixed outputs (positional)

**Setup:**
1. Output contract is defined:
   - output 0 → default routing
   - output 1 → topic routing (future)
   - output 2 → target routing
2. Node returns `[X1, X2, X3]`.

**Expected:**
- Output 0 routed by type.
- Output 1 routed by topic (if enabled).
- Output 2 routed by target.
- If no contract exists and strict mode is on → error.

### 6.6 Multiple payload types

**Setup:**
1. Consumer map: `X -> [B]`, `Y -> [C, D]`.
2. Outputs:
   - `Envelope(payload=X("x"), target="B")`
   - `Y("y")` (bare)

**Expected:**
- Deliveries: `[("B", X("x")), ("C", Y("y")), ("D", Y("y"))]`.

### 6.7 Invalid: empty outputs with required delivery

**Setup:**
1. Node contract declares `emits=[X]`.
2. Node returns `[]`.

**Expected:**
- Strict mode → error (misdeclared contract).
- Non‑strict → warning + drop.

### 6.8 Invalid: unknown target

**Setup:**
1. Envelope with `target="MissingNode"`.

**Expected:**
- Strict mode → error.
- Non‑strict → warning + drop.

### 6.9 Invalid: target with incompatible type

**Setup:**
1. `B` consumes only `X`.
2. Envelope payload is `Y`, target is `B`.

**Expected:**
- Strict mode → error.
- Non‑strict → warning + drop.

### 6.10 Fan‑out with list return

**Setup:**
1. Node returns `[X("x1"), X("x2")]`.

**Expected:**
- Router treats it as two messages.
- If a single message is intended → must return `Batch([x1, x2])`.
