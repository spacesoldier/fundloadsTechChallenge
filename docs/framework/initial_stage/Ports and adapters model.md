# Ports and adapters model (abstractions)

This document defines how **ports** and **adapters** are abstracted in the framework
and how they interact with routing and execution.

It complements:

- [Execution runtime and routing integration](Execution%20runtime%20and%20routing%20integration.md)
- [Execution planning model](Execution%20planning%20model.md)
- [Routing semantics](Routing%20semantics.md)
- [Service model](Service%20model.md)
- [File adapters and ordering model](File%20adapters%20and%20ordering%20model.md)

---

## 1) Port abstraction (absolute)

A port is a **pure abstraction** for data flow:

- it does **not** encode business logic
- it does **not** depend on concrete transport
- it exposes one of a few **minimal shapes**

Port-introduction policy:

- do not add project-specific ports for domain convenience;
- add a new port only when introducing a genuinely new transport primitive;
- model richer domain APIs as services over stable ports.

### 1.1 Stable port types

The framework supports a stable minimal set of port types:

- `stream` — ordered flow of models (object messages)
- `kv_stream` — ordered flow of `(key, value)` pairs
- `kv` — point state operations (`get/set/update`) for key/value state
- `request` — request-side call boundary
- `response` — response-side call boundary

Context persistence is treated as a `kv` use case (keyed metadata state),
not as a separate special transport type.

`source`/`sink` are not port types; they are runtime roles inferred from adapter
contracts (`consumes/emits`) and graph placement.

### 1.1.1 Network protocol mapping

Recommended mapping for network workloads:

- HTTP request handlers -> `request`
- HTTP response writers -> `response`
- WebSocket and server streaming -> `stream`
- keyed event protocols -> `kv_stream`

Protocol parsing/serialization is adapter responsibility, not node responsibility.

### 1.3 KV marker contracts

`kv` injections may use:

- `KVStore` base contract
- marker subclasses of `KVStore` (semantic role labels like `ContextKVStore`, `StateKVStore`)

Constraint:

- marker subclasses must not add new public methods beyond `KVStore` API (`get/set/delete`).
- if richer behavior is needed, it must be modeled as a `@service`, not by extending port API.

This rule is enforced at DI registration and `inject.kv(...)` call sites.

### 1.2 Why this split

- `stream`/`kv_stream` are transport-flow contracts.
- `kv` is state access, not event flow.
- `request`/`response` prepare framework-level integration for REST/MCP/queue RPC
  without leaking protocol details into nodes.

---

## 2) Adapters (concrete transport)

Adapters implement ports by talking to concrete systems:

- files
- Redis
- Kafka
- HTTP / MCP
- observability backends (stdout/jsonl/OTLP/Kafka/log stacks)

For planned network runtime support, platform adapters should include:

- FastAPI HTTP ingress/egress
- WebSocket ingress/egress
- HTTP/2 stream adapters
- GraphQL query/mutation adapters

Adapters are **payload-only**:

- they do not know about `Envelope`
- they do not do routing
- they do not interpret business rules

The framework wraps/unwraps payloads into `Envelope` at execution boundaries.

### 2.3 Observability adapters are platform-owned

Tracing/telemetry/monitoring sinks are infrastructure concerns and must be
declared/implemented in the framework layer (`stream_kernel`), not in project
domain ports. Project code should only emit business models; runtime and
platform adapters handle observability transport.

Framework observability model contracts are defined in:
- `src/stream_kernel/observability/domain/`

Default platform observability adapters are defined in:
- `src/stream_kernel/observability/adapters/`

### 2.1 Adapter identities come from framework discovery

Adapter instances are selected by YAML key name (`adapters.<name>`) and resolved
through framework discovery (`@adapter(name=...)`).
Project-specific factory paths are not part of the public config contract.

Initial file-oriented kinds:

- `file.line_reader` — streaming line-by-line reader (large-file friendly)
- `file.line_writer` — line-oriented writer

Detailed transport model for file payloads (`TextRecord`/`ByteRecord`), `seq`
ordering and parallel reorder strategy is defined in:

- [File adapters and ordering model](File%20adapters%20and%20ordering%20model.md)

The same document contains the active Stage A test-case map with direct links to
Python test files.

Rationale:

- "line" reflects Python's natural iterator-based file processing.
- Large files are processed incrementally; no full-file buffering is required by default.

### 2.2 No model/type strings in adapter config

Adapter YAML config must not declare model types/tokens as strings.
Type/model declarations stay in code (`@adapter`, mapping helpers), where
refactoring is safer and static tooling can help.

Config should describe only:

- adapter `settings`
- required `binds` by **port type** (not by model class name)

Example:

```yaml
adapters:
  ingress_file:
    settings:
      path: input.txt
      format: text/jsonl
    binds: [stream]
  egress_file:
    settings:
      path: output.txt
    binds: [stream]
```

```yaml
runtime:
  ordering:
    sink_mode: source_seq  # or completion
```

Notes:

- `settings.format` defaults to `text/jsonl` when omitted.
- `runtime.ordering.sink_mode=source_seq` enables strict sink-order guard based on transport `seq`.
- `runtime.ordering.sink_mode=completion` keeps current completion/FIFO drain semantics.

---

## 3) Routing over ports (preferred)

Instead of manually “writing to a port”, the preferred flow is:

1. Node emits **model objects**
2. Router routes by **model type**
3. The sink node receives the model and calls its adapter

This keeps routing centralized and avoids leaking infrastructure concerns into nodes.

---

## 3.1 Services over standard ports (no project-level domain ports)

Domain-specific "fat interfaces" should be modeled as **services**, not as a
parallel project-level port taxonomy.

Recommended layering:

- framework ports: transport contracts (`stream`, `kv_stream`, `kv`, `request`, `response`)
- adapters: concrete transport implementations
- services: domain API composed over those adapters
- nodes: business flow using services/injected dependencies

This avoids compatibility layers where the same behavior is declared twice
(framework port + project custom port).

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

### 5.5 Ack semantics (avoid node-level ack)

Ack should be handled by the **runtime/adapter boundary**, not by nodes:

- node code remains pure (no offset/ack bookkeeping)
- after successful routing/execution, the runtime signals `ack()` / `commit()`
- failed items can be retried or parked per runner policy

---

## 6) Test cases (TDD)

### 6.1 Port taxonomy enforcement

- accepted port types are exactly: `stream`, `kv_stream`, `kv`, `request`, `response`
- unknown port type in config fails fast
- `kv_stream` payload contract validates tuple form `(key, value)`
- `kv` contract validates point operations (no implicit iteration contract)

### 6.1.1 Network mapping conformance

- network ingress adapters bind to `request` or `stream` only
- network egress adapters bind to `response` or `stream` only
- GraphQL adapters are validated as `request`/`response` pairs

### 6.2 Adapters are payload-only

- adapter reads/writes models without Envelope
- runner is responsible for wrapping into Envelope

### 6.3 Framework-only adapter identity validation

- config with unsupported adapter name fails preflight
- config with supported adapter name passes and instantiates adapter
- discovery index resolves adapter by name without `factory` path

### 6.4 No model strings in config

- config containing model/class string for adapter contract is rejected
- contract/type mapping is taken from code metadata (`@adapter` + helper mapping)

### 6.5 Routing to sink nodes

- model `M` emitted
- sink node consumes `M` and writes to port
- adapter never sees Envelope

### 6.6 Async adapter facade

- node calls adapter synchronously
- adapter executes async internally
- node code stays sync

### 6.7 Open-end validation (missing adapters)

- graph has a consumed token with no provider
- no source adapter emits it
- expect build‑time error: “missing provider / missing adapter”

### 6.8 Multiple sources scheduler policy

- two source adapters emit the same model
- scheduler is round‑robin/priority/as‑available
- ordering is not guaranteed unless policy is explicit

### 6.9 Test coverage pointers

- `tests/stream_kernel/adapters/test_file_io.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/execution/test_context_service.py`
- `tests/stream_kernel/execution/test_runner_context_integration.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`

---

## 7) Implementation references

- `stream_kernel.adapters` (adapter metadata/registry, platform file adapters)
- `stream_kernel.integration` (work queue, context store, routing port)
- `stream_kernel.routing` (Envelope + Router)
- `stream_kernel.execution` (runner + planning)
