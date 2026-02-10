# Execution runtime and routing integration

This document describes how **Runner** and **Router** work together to execute
nodes, and how their integration is provided through ports/adapters.

It complements:

- [Routing semantics](Routing%20semantics.md)
- [Router + DAG roadmap](Router%20and%20DAG%20roadmap.md)

---

## 1) Why split Runner and Router

**Router** is pure routing logic:

- given an `Envelope` and a registry of consumers,
- decide which node(s) should receive the message,
- without executing any business logic.

**Runner** is execution logic:

- fetch a message from a queue,
- call the node,
- send outputs back to routing.

This split enables **multiple Runner implementations** (sync, async, distributed)
while keeping **one Router contract**.

Control-plane principle:

- Runner and Router are runtime control-plane components by default, not regular business DAG nodes.
- They may be represented as platform nodes only in an explicit platform control graph with dedicated queues.
- Do not mix business DAG semantics with runtime-control semantics in one node layer.

---

## 2) Integration diagram (default)

```
inputs → WorkQueue → Runner → Node → outputs → Router → WorkQueue
```

- `WorkQueue` is a port.  
- Router produces routing decisions.  
- Runner executes nodes and re‑enqueues outputs.

The loop terminates when the queue is empty (or at a max‑hops policy).

---

## 3) Source/sink adapter nodes (IO boundaries)

Adapter nodes are the **entry/exit points** of the graph:

- **Source node** emits a model type (`emits=[RawLine]`)
- **Sink node** consumes a model type (`consumes=[OutputLine]`)

Adapters remain **payload‑only**; the runner wraps/un‑wraps `Envelope`.

### 3.1 Implicit sink adapters

If an adapter consumes a model but is **not injected** into any node, it is
attached as an **implicit sink**:

- the adapter still receives routed messages for its consumed type
- the runtime emits a **diagnostic note** (warning/trace)

This is always on; strict mode does not change this behavior.

### 3.2 Pull‑mode

The sync runtime pulls one item at a time from the source adapter:

1. runner requests next item (`read()` / `poll(1)`)
2. wraps into `Envelope`
3. enqueues into `WorkQueue`

This preserves deterministic ordering and makes backpressure trivial.

### 3.3 Push‑mode

Async runtimes may let adapters push into queues:

- adapter produces payloads and enqueues via `WorkQueue.push`
- backpressure is enforced by queue capacity or a gate
- adapter may implement `ack()` / `commit()` semantics

### 3.4 Open‑end rule (no external tokens)

We do **not** introduce special “external input” tokens.  
Instead, the graph is required to be closed:

- every consumed token must be emitted by some node,
- if a token has no provider, a **source adapter node** must be attached,
- otherwise the build fails fast (missing provider / missing adapter).

---

## 4) Ports (core)

### 4.1 WorkQueue (message transport)

Minimal port contract:

- `push(envelope)`
- `pop() -> envelope | None`
- optional `size()` / `ack()`

#### WorkQueue behavior (logic)

- **FIFO** by default (deterministic for baseline runs).
- **Single message** per `pop()` call.
- **Empty pop** returns `None` (non-blocking default).
- **Backpressure hooks** (optional): enqueue limits or reject policy.
- **Partitioning** (future): queues per node or per pool.

### 4.1.1 Transport adapters behind queue port

`QueuePort` is intentionally transport-agnostic.
Default implementation is `InMemoryQueue` (deque), but the same contract can be
backed by:

- Redis stream/list,
- Kafka topic bridge,
- broker-native queue,
- in-process async queue.

Runner never depends on concrete transport details; queue backend is selected by runtime wiring.

### 4.2 ConsumerRegistry (routing registry)

The consumer registry is a **service port** that owns the dynamic mapping
between **message types** and **consumer nodes**.

Minimal contract:

- `get_consumers(token) -> list[node_name]`
- `has_node(name) -> bool`
- `list_tokens() -> list[type]`

This keeps **routing data** separate from both Router and Runner.

Runtime wiring rule:

- runtime/bootstrap binds `service<ApplicationContext>` once per scenario scope;
- `DiscoveryConsumerRegistry` (platform service) resolves consumer mapping from
  discovered node contracts;
- `RoutingPort` resolves registry via `inject.service(ConsumerRegistry)`;
- `SyncRunner` resolves only `service<RoutingPort>` (it does not receive
  consumer registry directly).

### 4.3 Context service over `kv`

Context handling is modeled as a **service** backed by the stable `kv` port.

- runner depends on `ContextService` (not on storage adapter lifecycle)
- context service uses `kv.get/set/delete` internally
- regular nodes receive metadata view; service nodes may request full context

This keeps execution generic and avoids introducing a context-specific transport port.

This keeps execution state portable across runtimes and ensures node-level
context is always available.

Implementation reference:

- [src/stream_kernel/integration/kv_store.py](../../../../src/stream_kernel/integration/kv_store.py)
- [src/stream_kernel/execution/runner.py](../../../../src/stream_kernel/execution/runner.py)

#### Context KV behavior (logic)

- Stores per‑message execution context (trace, retry metadata).
- Keyed by `trace_id` (or `msg_id` if no trace).
- **Idempotent** set/update expected.
- Optional TTL for cleanup in external stores.
- Default backend remains in-memory `dict`; planned backends include `cachetools` and Redis.

#### Envelope ↔ Context aggregation

- Every `Envelope` must carry a **context key** (e.g., `trace_id`).
- Runner retrieves context from KV storage before invoking a node.
- Node receives `payload` plus a **metadata section** (via `ctx`), not the full
  internal context object. This avoids accidental coupling to tracing internals.
- Full-context access (including tracing internals) is **not** exposed by
  default; it can be added later behind an explicit opt‑in API.
- Outputs inherit the same context key unless explicitly overridden.

---

## 4.4 Adapter config contract (runtime-facing)

Runtime resolves adapters from discovery/registry by adapter name (`adapters.<name>`).
Config must not contain per-adapter factory paths.

Config fields:

- `settings` — adapter settings payload
- `binds` — required port types (`stream`, `kv_stream`, `kv`, `request`, `response`)

Forbidden in config:

- model/type class paths
- routing contracts as free-form strings
- dynamic factory code references

Type/model mapping belongs to code metadata (`@adapter` + helper mapping).

---

## 5) Adapter variants

### 5.1 In-memory (default)

- `WorkQueue` backed by `collections.deque`
- `ConsumerRegistry` backed by in‑memory dict(s)
- context KV store backed by an in‑memory dict
- Best for tests, local runs, and deterministic CI

### 5.2 External (Redis, etc.)

- `WorkQueue` backed by Redis lists/streams
- `ConsumerRegistry` backed by Redis / external registry
- context KV store backed by Redis hashes
- Enables multi‑process scaling and recovery

Adapters live in the **framework**, but are wired by config.

---

## 6) Runner variants

### 6.1 SyncRunner (baseline)

- single‑threaded, processes queue until empty
- deterministic ordering
- ideal for baseline reference outputs

### 6.2 AsyncRunner

- `asyncio` based
- awaits IO‑bound nodes (HTTP, DB, MCP)
- same router contract

### 6.2.1 Network-bound workloads

Network interfaces (HTTP/WebSocket/GraphQL) should be modeled as adapters:

- ingress adapters convert protocol payload -> domain model and enqueue via queue port;
- business nodes stay protocol-agnostic;
- egress adapters convert domain model -> protocol response/stream.

This keeps FastAPI/WS/GraphQL integration in adapter layer and avoids runtime-specific branches.

### 6.3 DistributedRunner

- integrates with Celery/worker pools
- work items serialized through queue adapter
- context store required

### 6.3.1 Queue/topic duality

Point-to-point queue and pub/sub topic are distinct runtime semantics:

- queue: one consumer gets message;
- topic: multiple subscribers can consume same message.

If both are needed, use separate transport adapters under port contracts rather
than overloading one concrete queue implementation.

### 6.4 Runner interface (contract)

All runners implement a shared interface (`RunnerPort`):

- `run() -> None`
- consumes from WorkQueue until empty (or until a stop policy triggers)

This allows swapping runners without changing routing or application wiring.

---

## 7) Routing integration points

Runner responsibilities:

1. Pull `Envelope` from queue
2. Execute node
3. Normalize outputs into `Envelope` list
4. Ask **RoutingPort** for destinations
5. Push new envelopes into queue

Runner dependencies are injected through framework DI:

- `work_queue` via `inject.queue(Envelope, qualifier=\"execution.cpu\")`
- `routing_port` via `inject.service(RoutingPort)`
- `context_service` via `inject.service(ContextService)`

No manual object lifecycle must be hardcoded in runner construction.

RoutingPort responsibilities:

1. Normalize outputs to `Envelope`
2. Pull consumer map from `ConsumerRegistry`
3. Delegate pure routing to Router

Router responsibilities (pure logic):

1. If `Envelope.target` → deliver only to target(s)
2. Else route by `consumes/emits` (type fan‑out)

All **routing policy** lives in the Router.  
All **execution policy** lives in the Runner.

### 7.1 Execution-level tracing boundary

Execution observers (including tracing) are called by the runner around node invocation.
This defines a strict boundary:

- runner-targeted node call => one execution span;
- adapter attached to DAG as node => traced as its own node span;
- adapter injected inside node code => no standalone span unless explicitly modeled.

---

## 8) Test methodology (TDD)

### 7.1 Router‑only unit tests

- default fan‑out by type
- targeted routing override
- multi‑target order
- mixed outputs

### 7.1.1 ConsumerRegistry tests

- registration returns consumers by token
- unknown token returns empty list
- `has_node(name)` works for present/absent nodes

### 7.2 Runner + Router integration tests

- one input → expected fan‑out across nodes
- deterministic ordering with deque
- strict‑mode behavior for unknown targets
- adapter-node span exists when adapter is runner-targeted
- no extra span for injected adapter calls inside node body

### 7.3 Adapter tests

- in‑memory queue behavior
- external adapter contract (Redis stubbed)

---

## 9) Detailed test cases (TDD)

### 8.1 WorkQueue (in‑memory deque)

1. **FIFO order**
   - push A, then B
   - pop → A, then B
2. **Empty pop**
   - pop on empty queue → `None`
3. **Push after empty**
   - pop empty, then push A
   - pop → A

4. **FIFO under mixed producers**
   - push A, B, C (interleaved)
   - pop order matches push order

5. **Optional size**
   - size starts at 0
   - size increments on push
   - size decrements on pop

### 8.2 Context KV store (in‑memory dict)

1. **Set/Get/Delete**
   - set `trace_id=1`
   - get returns stored ctx
   - delete removes it
2. **Get missing**
   - get unknown id → `None`

3. **Idempotent update**
   - set ctx1
   - set ctx2 under same key
   - get returns ctx2

### 8.3 Router + Runner (sync) — baseline

1. **Single node, single input**
   - input → node consumes → emits one output
   - output delivered to sink exactly once
2. **Context lookup**
   - envelope carries `trace_id`
   - runner loads context before invoking node
   - node sees expected context values
3. **Fan‑out via router**
   - node emits `X`
   - two nodes consume `X`
   - both nodes receive the same payload in deterministic order
4. **Target override**
   - node emits `Envelope(target="C")`
   - only `C` receives it; `B` does not
5. **Mixed outputs**
   - output list with one targeted and one default
   - targeted → only target, default → fan‑out

### 8.4 Strict‑mode errors

1. **Unknown target**
   - `Envelope(target="Missing")` → error
2. **Incompatible type**
   - targeted payload type not consumed by target → error

### 8.5 Non‑strict mode (future)

1. **Unknown target**
   - warn + drop
2. **Incompatible type**
   - warn + drop

### 8.6 External queue adapter (Redis stub)

1. **Contract compliance**
   - push/pop round‑trip using stubbed backend
2. **Persistence boundary**
   - separate runner instances can pop what another pushed

### 8.7 Context KV external adapter (Redis stub)

### 8.8 Adapter config/no-factory path

1. **No factory reference in config**
   - config contains adapter `factory` key
   - runtime validator rejects config
2. **Name-based resolution**
   - config contains known adapter key under `adapters`
   - runtime resolves adapter from discovery registry
3. **Unknown adapter name**
   - config contains adapter key not discovered in modules
   - startup fails with explicit error

### 8.9 Stable port-type binding

1. **Accepted bind values**
   - each of `stream`, `kv_stream`, `kv`, `request`, `response` is accepted
2. **Unknown bind value**
   - `binds` contains unknown port type
   - startup fails with explicit error

1. **Set/Get round‑trip**
   - set ctx by key
   - get returns same ctx
2. **Overwrite**
   - put ctx1, then ctx2
   - get returns ctx2

---

## 10) Preflight self-test before run

Before runtime starts message processing, the framework runs a **preflight** step.

Preflight goals:

- fail fast on invalid graph contracts
- fail fast on ambiguous self-loop contracts
- provide actionable diagnostics before processing any real payload

Current preflight checks:

1. DAG validation (`consumes/emits` providers + cycle checks)
2. Contract safety check:
   - if node has overlapping tokens in `consumes` and `emits`
   - strict mode: fail with guidance
   - non-strict mode: allow for migration

### 10.1 Why this matters

Without preflight, the app can start and only fail at runtime after partial
processing. Preflight moves these failures to startup and keeps runs deterministic.

### 10.2 Preflight test cases (TDD)

1. **Valid graph passes**
   - source emits `RawToken`
   - transform consumes `RawToken`, emits `MidToken`
   - preflight succeeds

2. **Consumes/emits overlap fails in strict mode**
   - node consumes `X`, emits `X`
   - preflight raises error with migration hint

3. **Consumes/emits overlap allowed in non-strict mode**
   - same node contract as above
   - preflight returns without raising

4. **Runtime calls preflight before scenario build**
   - assert preflight hook is called once during `run_with_config`

5. **Deprecated runtime.pipeline is rejected**
   - config includes `runtime.pipeline`
   - runtime fails fast and asks to rely on contract-driven routing

---

## 9) Implementation references

- `stream_kernel.routing.router` (routing logic)
- `stream_kernel.integration.routing_port` (routing adapter)
- `stream_kernel.integration.work_queue` (deque adapter)
- `stream_kernel.integration.kv_store` (in-memory KV adapter)
- `stream_kernel.execution.context_service` (service facade over KV)
- `stream_kernel.execution` (runners, planned)

---

## 10) Runtime entrypoints

- `run(argv)` is the primary framework-first entrypoint.
- `run_with_config(config, ...)` is also framework-first by default and can
  resolve discovery/adapters from config without external wiring.
- Runtime bootstrap is expected to use execution builder APIs for artifact
  assembly (`stream_kernel.execution.builder`) and then execute via runner APIs.
