# Framework boundaries (initial stage)

This document captures the **initial boundary** between:

- the **framework** (generic runtime + infra ports/adapters)
- the **project** (domain + usecases + thin adapters)

It is intentionally minimal and may evolve as the extraction proceeds.

---

See also: [Application context and discovery](./Application%20context%20and%20discovery.md), [Node and stage specifications](./Node%20and%20stage%20specifications.md), [Injection registry](./Injection%20registry.md)

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
- Node contract
- DAG analysis and execution-plan assembly
- Tracing (recorder + sink interfaces)
- Generic CLI bootstrap (argument parsing + runtime assembly)
- Generic config loading (YAML/JSON + schema validation)

### 2.2 Generic ports (by communication type)

These ports are **protocol-level** contracts, not domain-specific:

- `stream`
- `kv_stream`
- `kv`
- `request`
- `response`
- `trace_sink`

The framework provides the abstract contracts and base utilities for these.

### 2.3 Generic adapters

Adapters that connect to infrastructure (not domain-specific):

- `file.line_source` / `file.line_sink`
- `kafka` (stream / kv_stream)
- `redis` (kv or kv_stream)
- `ignite` (kv)
- `stdout` / `jsonl` (sinks)

At this stage only **file/jsonl/stdout** exist in the project; others are
planned framework adapters.

---

### 2.4 Adapter registry (initial stage)

The framework owns a discovery-backed adapter registry that maps adapter names
to concrete implementations.

Initial scope:

- demo project uses neutral adapter roles (`ingress_file` / `egress_file`)
- framework resolves behavior by adapter contracts (`consumes` / `emits`), not by hardcoded names

Adapters are declared under `adapters.*` with:

- `settings`
- `binds` (port type list)

Runtime resolves adapters by discovered/registered adapter name (YAML key under `adapters`).
Config must not carry factory paths.

---

### 2.5 Test coverage methodology (adapter registry)

Tests should validate:

1) Adapter registry resolves known adapter names (file input/output).
2) Adapter registry rejects unknown adapter names with clear errors.
3) Settings are passed to adapters (e.g., file path).
4) Unknown adapter name fails fast.
5) Config containing factory path is rejected.

---

## 3) Project owns (initial scope)

- Domain model and parsing rules
- Usecase steps (business logic)
- Scenario composition (config + wiring)
- `main` that calls framework bootstrap (e.g., `app.run(...)`)
- **Thin adapters** that declare *how* they connect:
  - `@adapter(name="ingress_file", emits=[...])`
  - `@adapter(name="...", consumes=[...], emits=[...])`
- Domain-level services that use framework ports (e.g., `WindowStore` logic
  built on top of `kv`)

Project adapters **do not own IO**; they perform minimal validation and mapping,
then delegate to the framework adapter selected by config.

---

## 4) Notes on `WindowStore`

`WindowStore` is **domain-specific behavior**, not a framework port.

The framework provides `kv` (generic key/value access).
The project implements window semantics on top of that.

---

## 5) Decorators (later stage)

We plan to add decorators as **registration sugar** only:

- `@adapter(kind=..., consumes=..., emits=...)`
- `@adapter(name=..., consumes=..., emits=...)`
- `@node(name=...)`

Decorators must not hide wiring or introduce side effects at import time.

---

## 6) Implementation references

- Kernel + execution runtime: [src/stream_kernel/execution/runner.py](../../../../src/stream_kernel/execution/runner.py), [src/stream_kernel/execution/runner_port.py](../../../../src/stream_kernel/execution/runner_port.py), [src/stream_kernel/kernel/scenario.py](../../../../src/stream_kernel/kernel/scenario.py), [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py), [src/stream_kernel/execution/builder.py](../../../../src/stream_kernel/execution/builder.py)
- Application context + discovery: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py), [src/stream_kernel/kernel/discovery.py](../../../../src/stream_kernel/kernel/discovery.py)
- Nodes/stages metadata: [src/stream_kernel/kernel/node.py](../../../../src/stream_kernel/kernel/node.py), [src/stream_kernel/kernel/stage.py](../../../../src/stream_kernel/kernel/stage.py)
- Injection/config helpers: [src/stream_kernel/application_context/inject.py](../../../../src/stream_kernel/application_context/inject.py), [src/stream_kernel/application_context/injection_registry.py](../../../../src/stream_kernel/application_context/injection_registry.py), [src/stream_kernel/application_context/config_inject.py](../../../../src/stream_kernel/application_context/config_inject.py)

---

## 7) Test coverage methodology

The boundary decisions should be reflected by tests that:

Implementation tests (representative): [tests/stream_kernel/application_context/test_auto_discovery.py](../../../../tests/stream_kernel/application_context/test_auto_discovery.py), [tests/stream_kernel/application_context/test_application_context.py](../../../../tests/stream_kernel/application_context/test_application_context.py)

1) **Framework vs project split**
   - kernel + generic ports/adapters are under framework package.
   - project-only code (domain/usecases) does not import framework internals.

2) **Compatibility checks**
   - discovery finds framework annotations without requiring project wiring.
   - removing project wiring does not break runtime assembly.
