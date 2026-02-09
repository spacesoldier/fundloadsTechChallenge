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
2) discovers decorators (`@node`, `@stage`, `@adapter`, etc.),
3) builds a registry of steps/adapters/services,
4) injects config + dependencies,
5) returns a ready runtime graph for the composition root.

This becomes the replacement for `wiring.py`.

---

## 3) Discovery scope

Discovery will scan these project areas:

- `usecases` (steps and services)
- `adapters` (thin adapters tagged by framework adapter metadata)
- framework extension providers (e.g. observability adapters/observers)

The scan list is **explicit** (module paths from config) to avoid hidden
imports. Example:

```yaml
runtime:
  discovery_modules:
    - example_app.usecases.steps
    - example_app.adapters
```

Framework modules are appended via extension providers (`discovery_modules()`),
so runtime does not hardcode concrete observability module names.

---

## 4) Decorators (initial plan)

### 4.1 `@node`

Marks a class/function as a pipeline node:

- `name`: registry key
- optional: `consumes` (data types/tokens accepted)
- optional: `emits` (data types/tokens produced)
- optional: `config` (step-local config schema key)

### 4.2 `@adapter`

Marks an adapter with:

- `name`: stable adapter name used by discovery and config key matching
- optional `kind`: internal classifier (not part of YAML contract)
- `consumes/emits`: model contracts used by DAG/routing
- optional helper mapping hook in code (raw transport payload -> emitted model)

Adapters stay thin: validate/map, then delegate to framework infrastructure.

Important config rule:

- adapter YAML does **not** declare model/type strings;
- adapter is selected by YAML key name (`adapters.<name>`);
- YAML declares only `settings` and bound port types (`binds`).

### 4.3 `@service` (planned explicit marker)

Service components provide domain APIs over standard framework ports/adapters.
They are framework-managed and scenario-scoped by default.

Current practical path (already supported):

- declare service class/component
- inject it with `inject.service(ServiceImpl)`

Planned addition:

- explicit `@service(...)` decorator for clearer discovery/lint diagnostics

See: [Service model](./Service%20model.md)

---

## 5) Outcome: how wiring changes

- `wiring.py` becomes a thin call to `ApplicationContext.discover(...)`.
- Registry population is automatic based on decorators (`@node`, `@adapter`).
- Dependencies are resolved by injection contract (`port_type + data_type`),
  including service injections via `inject.service(...)`.
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
   - node registry is built from discovered nodes.
   - adapter registry is built from discovered adapters (no factory path in YAML).

3) **Scenario build**
   - step order matches config list (until DAG mode is introduced).
   - missing step in config produces a clear error.

4) **Adapter discovery/config contract**
   - unknown adapter name fails startup.
   - supported adapter name resolves without explicit factory reference.
   - adapter config contains no model/type class strings.

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
