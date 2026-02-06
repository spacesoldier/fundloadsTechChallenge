# Ports and adapters model (abstractions)

This document defines how **ports** and **adapters** are abstracted in the framework
and how they interact with routing and execution.

It complements:

- [Execution runtime and routing integration](Execution%20runtime%20and%20routing%20integration.md)
- [Execution planning model](Execution%20planning%20model.md)
- [Routing semantics](Routing%20semantics.md)

---

## 1) Port abstraction (absolute)

A port is a **pure abstraction** for data flow:

- it does **not** encode business logic
- it does **not** depend on concrete transport
- it exposes one of a few **minimal shapes**

### 1.1 Stream ports (object flow)

Ports that move **models** (domain objects):

- `stream_source` → yields models
- `stream_sink` → accepts models

### 1.2 KV stream ports (key/value flow)

Ports that move **key/value tuples**:

- `kv_stream_source` → yields `(key, value)`
- `kv_stream_sink` → accepts `(key, value)`

This keeps port contracts simple and uniform.

---

## 2) Adapters (concrete transport)

Adapters implement ports by talking to concrete systems:

- files
- Redis
- Kafka
- HTTP / MCP

Adapters are **payload‑only**:

- they do not know about `Envelope`
- they do not do routing
- they do not interpret business rules

The framework wraps/unwraps payloads into `Envelope` at execution boundaries.

---

## 3) Routing over ports (preferred)

Instead of manually “writing to a port”, the preferred flow is:

1. Node emits **model objects**
2. Router routes by **model type**
3. The sink node receives the model and calls its adapter

This keeps routing centralized and avoids leaking infrastructure concerns into nodes.

---

## 4) Sync facade for async adapters

Async adapters should be wrapped behind **sync facades**:

- node API stays synchronous
- async execution happens inside adapter
- execution planner routes node to async pool

This prevents `await` from spreading into business code.

---

## 5) Adapters as nodes (source/sink)

Adapters are **not nodes by default**.  
They become nodes only when they act as **sources** or **sinks** in the graph:

- **Source adapter node**: emits a model type (e.g., `RawLine`)
- **Sink adapter node**: consumes a model type (e.g., `OutputLine`)

This keeps the DAG clean and avoids “adapter logic” inside routing, while still allowing adapters to participate in **consumes/emits** wiring.

### 5.1 Source adapter nodes

Source adapters declare `emits=[Model]` and may have `consumes=[]`.  
They **participate in the graph** as entry points; they are not “special‑cased.”

**Open‑end rule (strict):**
- Every consumed token must be emitted by some node.
- If a token has no provider, the system must either:
  - attach a source adapter that emits it, or
  - fail fast at build time.

This keeps the graph closed without introducing special “external token” types or any other special rules.

### 5.2 Sink adapter nodes

Sink adapters declare `consumes=[Model]`, `emits=[]`.
They receive the model through routing and write it to the transport.
They never see `Envelope`; the runner unwraps payloads.

**Implicit sink rule (always on):**

If an adapter consumes a model but is **not injected** into any node, it is
still attached to the graph as an **implicit sink**:

- it receives routed messages like any other sink
- the runtime emits a **diagnostic note** (warning/trace) that the sink
  was connected implicitly

This keeps behavior explicit without requiring manual sink wiring.

### 5.3 Pull vs push sources (execution contract)

We support two **source modes** (adapter-specific):

- **Pull**: runner asks for the next message (deterministic, backpressure friendly).
- **Push**: adapter produces messages and the runtime enqueues them.

The default (baseline) mode is **pull** to preserve deterministic ordering.

Adapters can expose:

- `read()` / `poll(n)` for pull
- `subscribe(callback)` for push (async runtimes)
- optional `ack()` / `commit()` for offset-based sources (Kafka, etc.)

The **port contract** should define these methods explicitly, not by convention.

### 5.4 Multiple sources of the same model

If multiple sources emit the same model:

- treat it as **fan‑in** at the token level,
- merge via a scheduler (round‑robin, priority, or “as available”),
- document the ordering policy (no implicit guarantees).

### 5.5 Ack semantics (avoid node‑level ack)

Ack should be handled by the **runtime/adapter boundary**, not by nodes:

- node code remains pure (no offset/ack bookkeeping)
- after successful routing/execution, the runtime signals `ack()` / `commit()`
- failed items can be retried or parked per runner policy

---

## 6) Test cases (TDD)

### 6.1 Port shape enforcement

- stream ports accept model objects only
- kv ports accept `(key, value)` tuples only

### 6.2 Adapters are payload‑only

- adapter reads/writes models without Envelope
- runner is responsible for wrapping into Envelope

### 6.3 Routing to sink nodes

- model `M` emitted
- sink node consumes `M` and writes to port
- adapter never sees Envelope

### 6.4 Async adapter facade

- node calls adapter synchronously
- adapter executes async internally
- node code stays sync

### 6.5 Open‑end validation (missing adapters)

- graph has a consumed token with no provider
- no source adapter emits it
- expect build‑time error: “missing provider / missing adapter”

### 6.6 Multiple sources scheduler policy

- two source adapters emit the same model
- scheduler is round‑robin/priority/as‑available
- ordering is not guaranteed unless policy is explicit

---

## 7) Implementation references (to be added)

- `stream_kernel.integration` (ports + adapters)
- `stream_kernel.routing` (Envelope + Router)
- `stream_kernel.execution` (runner + planning)
