# Application context and discovery (initial stage)

This document replaces the manual `wiring.py` approach with a **Spring-like
application context** that performs **discovery** and **registration** based on
decorators.

We intentionally allow "framework magic" in this branch to evaluate the
developer experience.

---

## 1) What wiring.py does today (baseline behavior)

`src/fund_load/usecases/wiring.py` currently:

- builds a `StepRegistry`
- registers every step explicitly by name
- injects dependencies (ports/services) from a `wiring` dict
- pulls config values from `AppConfig` (features, policies, windows)

### Steps registered today

- `parse_load_attempt` → `ParseLoadAttempt()`
- `compute_time_keys` → `ComputeTimeKeys(week_start=...)`
- `idempotency_gate` → `IdempotencyGate()`
- `compute_features` → `ComputeFeatures(...)` (uses `prime_checker`)
- `evaluate_policies` → `EvaluatePolicies(...)` (uses `window_store`)
- `update_windows` → `UpdateWindows(...)` (uses `window_store`)
- `format_output` → `FormatOutput()`
- `write_output` → `WriteOutput(output_sink=...)`

### Dependencies used

- `prime_checker` (from wiring)
- `window_store` (from wiring)
- `output_sink` (from wiring)

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
    - fund_load.usecases.steps
    - fund_load.adapters
```

---

## 4) Decorators (initial plan)

### 4.1 `@step`

Marks a class/function as a pipeline step:

- `name`: registry key
- optional: `requires` (dependency names)
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
- Dependencies are resolved by name (same keys as today: `prime_checker`,
  `window_store`, `output_sink`).
- Configuration is applied by the context, not by hand-coded lambdas.

---

## 6) Compatibility note

This approach overrides the earlier **no-magic** rule for the purposes of
framework extraction. If adopted, the project-level wiring will be simplified
to module discovery + config-driven composition.
