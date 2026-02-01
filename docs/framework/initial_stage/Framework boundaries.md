# Framework boundaries (initial stage)

This document captures the **initial boundary** between:

- the **framework** (generic runtime + infra ports/adapters)
- the **project** (domain + usecases + thin adapters)

It is intentionally minimal and may evolve as the extraction proceeds.

---

## 1) Guiding principles

- Kernel and generic infrastructure belong to the framework.
- Business meaning belongs to the project.
- Ports are **generic** and live in the framework.
- Project code **does not declare custom ports** at this stage.
- Project adapters are **thin**: validate / map domain fields, then delegate.
- No framework magic: registration is explicit; decorators are only sugar.

**Note (experimental branch):** for framework extraction we intentionally
override some constraints from the original test project, including the
"no framework magic" rule. This branch is allowed to introduce discovery and
decorator-driven registration so we can evaluate a Spring-like developer
experience. If adopted, these changes will supersede the earlier guidance.

---

## 2) Framework owns (initial scope)

### 2.1 Kernel runtime

- Scenario execution and orchestration
- Context lifecycle
- Step contract
- Scenario builder and step registry
- Tracing (recorder + sink interfaces)
- Generic CLI bootstrap (argument parsing + runtime assembly)
- Generic config loading (YAML/JSON + schema validation)

### 2.2 Generic ports (by communication type)

These ports are **protocol-level** contracts, not domain-specific:

- `stream_source`
- `stream_sink`
- `kv_stream_source`
- `kv_stream_sink`
- `kv_store`
- `trace_sink`

The framework provides the abstract contracts and base utilities for these.

### 2.3 Generic adapters

Adapters that connect to infrastructure (not domain-specific):

- `file` (stream source/sink)
- `kafka` (stream / kv_stream)
- `redis` (kv_store or kv_stream)
- `ignite` (kv_store)
- `stdout` / `jsonl` (sinks)

At this stage only **file/jsonl/stdout** exist in the project; others are
planned framework adapters.

---

## 3) Project owns (initial scope)

- Domain model and parsing rules
- Usecase steps (business logic)
- Scenario composition (config + wiring)
- `main` that calls framework bootstrap (e.g., `app.run(...)`)
- **Thin adapters** that declare *how* they connect:
  - `@adapter(type=file, flow=stream_source)`
  - `@adapter(type=memory, flow=kv_store)`
- Domain-level services that use framework ports (e.g., `WindowStore` logic
  built on top of `kv_store`)

Project adapters **do not own IO**; they perform minimal validation and mapping,
then delegate to the framework adapter selected by config.

---

## 4) Notes on `WindowStore`

`WindowStore` is **domain-specific behavior**, not a framework port.

The framework provides `kv_store` (generic key/value access).
The project implements window semantics on top of that.

---

## 5) Decorators (later stage)

We plan to add decorators as **registration sugar** only:

- `@adapter(type=..., flow=...)`
- `@step(name=...)`

Decorators must not hide wiring or introduce side effects at import time.
