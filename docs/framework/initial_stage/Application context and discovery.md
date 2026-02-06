# Application context and discovery (initial stage)

This document replaces the manual `wiring.py` approach with a **Spring-like
application context** that performs **discovery** and **registration** based on
decorators.

We intentionally allow "framework magic" in this branch to evaluate the
developer experience.

---

See also: [Scenario vs node axes](./Scenario%20vs%20node%20axes.md), [Node and stage specifications](./Node%20and%20stage%20specifications.md), [Auto-discovery policy](./Auto-discovery%20policy.md), [Factory and injection model](./Factory%20and%20injection%20model.md), [Injection and strict mode](./Injection%20and%20strict%20mode.md), [Injection registry](./Injection%20registry.md)

---

## 1) What manual wiring does today (baseline behavior)

In a manually wired project, the wiring module typically:

- builds a `StepRegistry`
- registers every step explicitly by name
- injects dependencies (ports/services) from a `wiring` dict
- pulls config values from an app config model

### Example steps (generic)

- `parse_input` → `ParseInput()`
- `compute_time_keys` → `ComputeTimeKeys(...)`
- `deduplicate` → `Deduplicate(...)`
- `compute_features` → `ComputeFeatures(...)` (uses `feature_checker`)
- `evaluate_policies` → `EvaluatePolicies(...)` (uses `window_store`)
- `update_state` → `UpdateState(...)`
- `format_output` → `FormatOutput()`
- `write_output` → `WriteOutput(output_sink=...)`

### Dependencies used (generic)

- `feature_checker`
- `window_store`
- `output_sink`

---

## 2) New concept: ApplicationContext

**ApplicationContext** is a framework-level component that:

1) imports configured modules (usecases + adapters),
2) discovers decorators (`@step`, `@adapter`, etc.),
3) builds a registry of steps/adapters/services,
4) injects config + dependencies,
5) returns a ready runtime graph for the composition root.

This becomes the replacement for `wiring.py`.

---

## 3) Discovery scope

Discovery will scan these project areas:

- `usecases` (steps and services)
- `adapters` (thin adapters tagged by flow/type)

The scan list is **explicit** (module paths from config) to avoid hidden
imports. Example:

```yaml
discovery:
  modules:
    - example_app.usecases.steps
    - example_app.adapters
```

---

## 4) Decorators (initial plan)

### 4.1 `@step`

Marks a class/function as a pipeline step:

- `name`: registry key
- optional: `consumes` (data types/tokens accepted)
- optional: `emits` (data types/tokens produced)
- optional: `config` (step-local config schema key)

### 4.2 `@adapter`

Marks an adapter with:

- `type`: file/kafka/redis/etc
- `flow`: stream_source / stream_sink / kv_stream_source / kv_stream_sink / kv_store

Adapters stay thin: validate/map, then delegate to framework infrastructure.

---

## 5) Outcome: how wiring changes

- `wiring.py` becomes a thin call to `ApplicationContext.discover(...)`.
- Registry population is automatic based on decorators.
- Dependencies are resolved by name (e.g., `feature_checker`,
  `window_store`, `output_sink`).
- Configuration is applied by the context, not by hand-coded lambdas.

---

## 6) Compatibility note

This approach overrides the earlier **no-magic** rule for the purposes of
framework extraction. If adopted, the project-level wiring will be simplified
to module discovery + config-driven composition.

---

## 7) Implementation references

- Application context assembly: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)
- Discovery and metadata: [src/stream_kernel/kernel/discovery.py](../../../../src/stream_kernel/kernel/discovery.py), [src/stream_kernel/kernel/node.py](../../../../src/stream_kernel/kernel/node.py), [src/stream_kernel/kernel/stage.py](../../../../src/stream_kernel/kernel/stage.py)
- Scenario build + registry: [src/stream_kernel/kernel/scenario_builder.py](../../../../src/stream_kernel/kernel/scenario_builder.py), [src/stream_kernel/kernel/step_registry.py](../../../../src/stream_kernel/kernel/step_registry.py)
- Injection/config: [src/stream_kernel/application_context/inject.py](../../../../src/stream_kernel/application_context/inject.py), [src/stream_kernel/application_context/injection_registry.py](../../../../src/stream_kernel/application_context/injection_registry.py), [src/stream_kernel/application_context/config_inject.py](../../../../src/stream_kernel/application_context/config_inject.py)

---

## 8) Test coverage methodology

Tests should validate:

Implementation tests: [tests/stream_kernel/application_context/test_application_context.py](../../../../tests/stream_kernel/application_context/test_application_context.py), [tests/stream_kernel/application_context/test_application_context_registry.py](../../../../tests/stream_kernel/application_context/test_application_context_registry.py), [tests/stream_kernel/application_context/test_application_context_scenario.py](../../../../tests/stream_kernel/application_context/test_application_context_scenario.py), [tests/stream_kernel/application_context/test_application_context_stages.py](../../../../tests/stream_kernel/application_context/test_application_context_stages.py)

1) **Discovery path**
   - modules are scanned and annotated nodes are found.
   - duplicate node names fail fast.

2) **Context assembly**
   - data contracts are validated (`consumes/emits` are coherent).
   - registry is built from discovered nodes.

3) **Scenario build**
   - step order matches config list (until DAG mode is introduced).
   - missing step in config produces a clear error.

---

## 9) Factory resolution rules (function nodes)

Function nodes may be **plain steps** or **factories**. The framework decides
at **build time** how to interpret them.

### 9.1 Factory contract

- `(cfg: dict[str, object]) -> step`
- called **once per scenario**
- returned step is used for the entire stream

**Resolution rule (current):**

- the framework **attempts to call** the function with `cfg`
- if the call raises `TypeError`, the function is treated as a **plain step**
- if the call succeeds, the return value **must be callable**

### 9.2 Why build-time resolution

Resolution happens in ApplicationContext/ScenarioBuilder to:

- keep runtime deterministic (no per-message factory calls)
- avoid side effects during module import
- centralize validation of factory output (must be callable)

### 9.3 Injection and factories

Dependencies are injected **after** step construction via `@inject`.
Factories receive **config only**, not wiring.

See: [Factory and injection model](./Factory%20and%20injection%20model.md)
