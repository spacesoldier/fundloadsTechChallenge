# DAG construction (consumes/emits)

This document defines how we build a **DAG for analysis** from `consumes/emits`.
It does **not** define runtime routing (that is handled by the router).
For runtime routing rules, see [Routing semantics](Routing%20semantics.md).

---

## 1) Scope

The DAG is used for:

- cycle detection
- topology visualization
- trace analysis (expected vs actual flows)
- validation of missing providers

It is **not** the execution order. Routing is dynamic.

---

## 2) Inputs

For each node:

- `consumes`: list of type tokens (non‑empty for **non‑source** nodes)
- `emits`: list of type tokens (can be empty for sinks)

`consumes/emits` are the only supported names in the framework docs and code.

**Source node** (entry point):

- `consumes=[]`
- `emits=[Model]`
- used to close open ends by attaching adapter sources

### 2.0 Open‑end rule (no external tokens)

We do **not** use special “external input” tokens.  
Instead, the graph must be **closed**:

- every consumed token must be emitted by some node,
- if a token has no provider, attach a **source adapter node** that emits it,
- if no such adapter exists, DAG validation fails.

### 2.1 Multiple emit paths

`emits` represents **all possible output types** a node may produce.
Nodes may emit different types depending on logic (routing, filtering, policy, etc.).
The DAG uses the **union** of these possible outputs.

This means:

- edges exist even if a type is emitted only in rare branches,
- runtime routing still decides what actually gets delivered.

### 2.2 Fan-out vs "list as a message"

At the step contract level, returning a **list** means **fan-out**:
each list item is treated as an separate mesage and routed independently.

If a node needs to emit a **list as a single message**, it must wrap it in a dedicated type (e.g., `Batch`), otherwise the router cannot distinguish
"list-as-message" from "list-of-messages".

---

## 3) Graph definition

We build a bipartite-ish view and then collapse into a node graph:

- For each token `T`:
  - providers: all nodes that **emit** `T`
  - consumers: all nodes that **consume** `T`
  - we create edges `provider -> consumer` for all pairs

This yields a **directed graph** `G = (V, E)` over nodes.

---

## 4) Validation rules

1) **No empty consumes (except sources)**  
   Every non‑source node must declare at least one consumed type.

2) **Missing providers**  
   If a token is consumed but never emitted by any node, fail fast.

3) **Cycles**  
Cycles are detected and reported. Cycles do **not** imply runtime failure
by themselves, but they must be visible to operators.

3.1) **Same token on input and output of one node (`consumes=[T], emits=[T]`)**  
This creates a **self-loop** in the analytic DAG and is treated as a cycle.
In practice this is a common corner case for transitional flows
(e.g. an update step that receives and returns the same model).

Resolution options:

- introduce stage-specific model tokens (`DecisionIn` -> `DecisionOut`)
- keep one model token, but use explicit `Envelope.target` contracts

4) **Determinism**  
   When iterating nodes or edges, we use **discovery order** for stability.

---

## 5) What the DAG is not

The DAG does **not** enforce execution order.
It exists for **analytics** and **safety checks** only.

Runtime routing can still create feedback loops; those are handled by
router policies (max‑hops / retry limits).

---

## 6) Test cases (TDD)

### 6.1 Happy path: fan‑out

- A emits `X`
- B consumes `X`
- C consumes `X`
- Expect edges: `A->B`, `A->C`

### 6.2 Happy path: fan‑in

- A emits `X`
- B emits `X`
- C consumes `X`
- Expect edges: `A->C`, `B->C`

### 6.2.1 Multiple consumes (one node, many types)

- A emits `X`, `Y`
- B consumes `X`, `Y`
- Expect edges: `A->B` (both types map to the same consumer)

### 6.3 Missing provider

- A consumes `X`
- no node emits `X`
- Expect validation error

### 6.4 Cycle detection

- A emits `X`, consumes `Y`
- B emits `Y`, consumes `X`
- Expect cycle reported

### 6.4.1 Cycle: self-loop

- A emits `X`, consumes `X`
- Expect cycle reported (self-loop)
- Note: runtime may still avoid accidental infinite loops via router self-loop protection
  on **default fan-out**; explicit self-target is still possible by design.

### 6.4.2 Cycle: figure‑eight / overlapping cycles

- A emits `X`, consumes `Y`
- B emits `Y`, consumes `X`  (cycle 1: A<->B)
- B emits `Z`, consumes `W`
- C emits `W`, consumes `Z`  (cycle 2: B<->C)
- Expect cycles reported (overlapping at B)

### 6.4.3 Cycle with fan‑in/fan‑out (larger graph)

Example with 6 nodes:

- A emits `X`, consumes `Y`
- B emits `Y`, consumes `X`   (A<->B cycle)
- C emits `X`                (fan‑in into the same token as A)
- D consumes `X`, emits `Z`  (fan‑out from `X`)
- E consumes `Z`, emits `W`
- F consumes `W`, emits `Y`  (completes a larger cycle via `Y`)

Expect cycle reported (A‑B and extended loop through D‑E‑F).

### 6.5 Deterministic ordering

- Discovery order is A, B, C
- Unrelated nodes should keep stable order in graph outputs

### 6.6 Empty consumes (invalid for non‑sources)

- Non‑source node consumes []
- Expect validation error

### 6.6.1 Source adapters close open ends

- Source node consumes `[]`, emits `RawLine`
- `RawLine` is consumed by pipeline nodes
- Expect graph builds without missing‑provider error

### 6.7 List is fan‑out (routing contract)

- A emits `X` and returns `[x1, x2]`
- B consumes `X`
- Expect B receives two separate messages (x1, x2)

### 6.8 List-as-message uses a wrapper

- A emits `Batch` and returns `Batch([x1, x2])`
- B consumes `Batch`
- Expect B receives a single message (Batch), not two

---

## 7) Implementation references (to be added)

- DAG builder module (planned)
- ApplicationContext integration (planned)
