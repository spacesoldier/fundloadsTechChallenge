## Guide contents

_A deep, practice-driven guide blending Ruby best practices, Hexagonal Architecture, DDD, and streaming/reactive patterns under the reality of the GIL._

---

### 0. Preface

- Who this guide is for: Ruby engineers building/maintaining Python systems (or reviewing them)
- What “Ruby-friendly Python” means (and what it does **not** mean)
- Constraints and goals:
    - readability and composability first
    - predictable behavior > cleverness
    - streaming correctness > micro-optimizations
    - GIL-aware concurrency model

---

## Part I — Shared Values: Ruby Craftsmanship Mapped to Python

### 1. Ruby Engineering Values That Transfer

- Small objects, single responsibility, “tell, don’t ask”
- Composition over inheritance
- Explicit boundaries and minimal coupling
- Convention, naming, and intent-revealing APIs
- Testing culture: fast unit tests + purposeful integration tests
- Avoiding “action-at-a-distance” and hidden global state

### 2. Python Pitfalls Ruby Folks Hate (and how to avoid them)

- “Script soup”: free-floating functions, implicit dependencies
- Globals, mutable dicts everywhere, ad-hoc schemas
- Try/except used as flow control
- Side effects in the middle of pure transforms
- Overusing metaprogramming / magic decorators without contracts
- Framework-centric architecture (business logic living inside adapters)

### 3. The “Ruby-Friendly” Python Style Guide

- Naming conventions that read like a pipeline DSL
- Prefer dataclasses/value objects over raw dicts
- Prefer protocols/ports over concrete dependencies
- Prefer tiny modules over `utils.py`
- Structured logging, explicit error types, explicit IO boundaries
- Type hints as contracts (pragmatic, not dogmatic)

---

## Part II — Architectural Backbone: Hexagonal + DDD for Streaming

### 4. Architecture Overview: Hexagonal for a Streaming System

- Domain vs Application vs Adapters vs Runtime
- Ports and adapters: inbound/outbound ports
- Dependency rule: domain depends on nothing
- Why hexagonal is especially useful in streaming (testability + replaceable IO)

### 5. DDD for Event Streams (Practical, Not Religious)

- What belongs in the Domain Model in a streaming context
- Ubiquitous language: Payment, Feature, Window, Aggregation, Offset, Partition
- Entities vs Value Objects (what your payments/aggregates actually are)
- Domain services vs application services (where pipeline steps live)
- Bounded contexts: ingestion vs enrichment vs settlement vs reporting
- Context mapping patterns (upstream/downstream, translation layers)

### 6. Modeling the Message

- Message envelope vs payload:
    - trace id, received_at, partition, offset, headers
- Immutable domain objects + mutable processing context
- Schema strategy:
    - typed dataclasses / pydantic models
    - versioning and backward compatibility
- Domain errors as first-class types

---

## Part III — Pipeline as a Product: A DSL for Transformations

### 7. Pipeline Semantics (What Your System Promises)

- Transform / filter / fan-out / join / sink
- Exactly-once vs at-least-once vs at-most-once (and what you actually implement)
- Ordering guarantees:
    - global ordering is a myth
    - per-key ordering via partitioning
- Determinism and replayability (the Ruby “clean room” mindset)

### 8. Step Contract Design (the Core Abstraction)

- Step as a callable object (map/filter/tap semantics)
- One-in → zero/one/many-out (Iterable model)
- Side-effect discipline: `Tap` and `Sink` steps
- Pure steps vs stateful steps (explicit classification)
- Error signaling policy:
    - drop with reason
    - route to DLQ
    - retry with backoff
    - fail-fast

### 9. Registry + External Configuration (Composable Systems)

- Why config should compose, not encode logic
- Step registry: `name -> factory`
- Parameter validation and schema
- Safe dynamic composition:
    - allowlist only
    - versioned step names
- Feature toggles vs pipeline composition
- Environment overrides (12-factor style)

---

## Part IV — Streaming & “Reactive” Thinking Without Magical Frameworks

### 10. Reactive Principles Applied Pragmatically

- Backpressure: what it means outside of a reactive framework
- Time vs event ordering
- Watermarks (conceptually) and late events (what you do when events arrive late)
- Observability as part of the stream: traces, metrics, structured logs

### 11. Windowed Aggregations as Stateful Steps

- Tumbling vs sliding vs session windows
- Keyed state: per account_id buffers
- State eviction: memory caps, TTL
- Deterministic aggregation and replay
- Exactly-once illusions: idempotent aggregates, dedup keys

### 12. Feature Activation & Enrichment

- Feature calculation design:
    - pure formulas
    - enriched message type
- External enrichment:
    - cache strategy
    - timeout and fallback semantics
    - circuit breakers (simple, explicit)
- Join patterns: lookup join vs stream-stream join (and why stream-stream is hard)

---

## Part V — Concurrency Under the GIL: Correctness First

### 13. The GIL Reality Check

- What threads can and cannot do
- CPU-bound vs IO-bound in Python
- When to use:
    - threads
    - asyncio
    - multiprocessing
- Ruby MRI parallel: how it maps, where it differs

### 14. Concurrency Patterns That Work for Streaming Pipelines

- Ingest → queue → workers → sink (classic)
- Per-key partition routing to preserve order
- Bounded queues for backpressure
- Worker lifecycle and graceful shutdown
- Multiprocessing for CPU-heavy stages (selective offloading)
- Avoid shared mutable state; isolate state per worker/partition

### 15. Delivery Semantics: Offsets, Acks, and Idempotency

- Offset commit strategies:
    - after-parse
    - after-transform
    - after-sink (typical)
- At-least-once delivery + idempotent sink
    
- Dedup keys and idempotency stores
    
- Retry taxonomy:
    - transient vs permanent errors
    - poison pills and DLQ
- Transaction boundaries (when supported by broker/sink)

---

## Part VI — Adapters: Kafka, HTTP, DB, Files (Ports Stay Clean)

### 16. Adapter Design Rules

- Keep adapters stupid: translate + call ports
- Avoid leaking library types into core
- Connection management and timeouts
- Serialization strategy
- Observability in adapters (but not business logic)

### 17. Source Adapters

- Kafka consumer design: partitions, polling, batching
- File replay mode: deterministic local testing
- HTTP ingress (if applicable): validation and rate limiting

### 18. Sink Adapters

- Kafka producer: keys, headers, retries
- DB sink:
    - idempotent upserts
    - batching
    - deadlocks and retry policy
- Redis: offsets/idempotency caches

---

## Part VII — Testing, Quality, and Operability (Ruby-Level Discipline)

### 19. Testing Strategy (RSpec mindset in pytest)

- Unit tests for steps (pure + stateful)
- Contract tests for ports
- Integration tests for adapters (containers)
- Replay tests: golden files, deterministic runs
- Property tests for window logic (optional)

### 20. Tooling & Standards

- Formatting/linting: ruff, mypy (pragmatic)
- CI pipeline: fast checks first, slow later
- Type hints as maintainable contracts (not “types everywhere”)
- Performance testing that matters: throughput, latency, memory, tail latencies

### 21. Observability & Operations

- Structured logs with correlation IDs
- Metrics: per-step throughput, drops, retries, queue depth
- Tracing boundaries
- Runbooks: failure modes and recovery
- Feature flags and safe rollout patterns

---

## Part VIII — Reference Implementation Walkthrough (End-to-End)

### 22. Building the Reference Project

- Project structure (domain/pipeline/runtime/adapters/config)
- Minimal viable pipeline
- Add filtering rules
- Add feature activation
- Add window aggregation
- Add Kafka in/out
- Add offset store + retry + DLQ
- Add partitioned workers
- Add local replay + deterministic tests

### 23. “How to Extend” Cookbook

- Add a new step
- Add a new rule predicate
- Add a new feature group
- Add a new window
- Add a new sink adapter
- Add selective multiprocessing stage
- Add a new pipeline configuration safely

---

## Appendices

### A. Glossary (Ruby ↔ Python Translation)

- enumerables → iterables, blocks → callables, mixins → composition, etc.

### B. “Do / Don’t” Quick Reference

- 2–3 pages of hard rules
    

### C. Templates

- Step skeleton
- Adapter skeleton
- Config schema skeleton
- Test skeleton

### D. Design Checklists

- Step review checklist
- Adapter review checklist
- Operational readiness checklist