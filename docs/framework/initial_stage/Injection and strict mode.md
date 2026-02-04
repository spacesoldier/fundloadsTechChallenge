# Injection and strict mode (initial stage)

This document defines dependency injection (`@inject`) and configuration
resolution, including **strict mode** defaults.

---

## 1) Goals

- Provide explicit dependencies to nodes without manual wiring.
- Allow node parameters to be sourced from application config.
- Fail fast by default when required values are missing.

---

## 2) `@inject` concept

`@inject` marks a dependency that must be provided by the application context.

Use cases:

- `state_store`
- `feature_checker`
- `output_sink`

### 2.1 Type-aware injection (preferred)

Dependencies are resolved by **port type + data type**:

```python
class OrderEvent: ...
class UserState: ...

stream = inject.stream(OrderEvent)   # stream_source<OrderEvent>
store = inject.kv(UserState)         # kv_store<UserState>
```

This allows the framework to keep ports generic while providing
type-safe resolution. Resolution uses `(port_type, data_type)` as a key.

Strict mode applies if no match is found.

The injection system resolves these by name from the context wiring.

If a required dependency is missing:

- **strict mode** → fail fast
- **non-strict mode** → warning + `None` (or a default stub)

---

## 3) Config value injection

We support config-driven initialization of nodes.

Preferred initial design: **descriptor-based fields** (Pythonic and readable):

```python
class ComputeFeatures:
    monday_multiplier = config.value(
        \"features.monday_multiplier.multiplier\",
        default=Decimal(\"1.0\")
    )
```

The `config.value(...)` marker behaves similarly to `inject.stream(...)`:
it is resolved at scenario build time and replaced with the actual value.

Alternative (future): `typing.Annotated` metadata.

Config resolution rules:

- Node config is a **slice** of the app config.
- A node resolves `config.value("x.y")` from its own slice by default.
- `config.value("global.x.y")` explicitly reads from the global scope.
- If there is no explicit `global` section, the top-level config acts as
  a global fallback (for backwards compatibility).
- Missing values are handled by strict mode (see below).

### 3.1 Config shape (initial stage)

```yaml
strict: true
config:
  nodes:
    node_a:
      limit: 5
    node_b:
      limit: 9
  global:
    timezone: UTC
```

Notes:

- `nodes.<node_name>` is the node slice.
- `global` is the shared scope.
- In **strict** mode, a node cannot read from another node’s slice.
- In **non-strict** mode, explicit access to `global.nodes.<other>.*` is allowed,
  as a compatibility escape hatch.

See: [Factory and injection model](Factory%20and%20injection%20model.md), [Injection registry](Injection%20registry.md)

---

## 4) Strict mode

Strict mode is **enabled by default**.

Purpose:

- catch missing config/dependency issues early
- ensure deterministic runtime behavior

Override through app config:

```yaml
runtime:
  strict: false
```

Semantics:

- missing dependency or config value → error (strict)
- missing dependency or config value → warning + default/None (non-strict)

### 4.1 Strict vs non-strict config cross-access

Examples:

```python
@node(name="a")
class A:
    # OK in both modes: explicit global.
    tz = config.value("global.timezone")

    # Only allowed in non-strict mode.
    other_limit = config.value("global.nodes.b.limit")
```

- **strict** → error when resolving `global.nodes.b.limit`
- **non-strict** → allowed; resolves if present, else `None`

---

## 5) Implementation references

- Injection descriptors: [src/stream_kernel/application_context/inject.py](../../../../src/stream_kernel/application_context/inject.py)
- Injection registry + scope: [src/stream_kernel/application_context/injection_registry.py](../../../../src/stream_kernel/application_context/injection_registry.py)
- Config descriptors: [src/stream_kernel/application_context/config_inject.py](../../../../src/stream_kernel/application_context/config_inject.py)
- Application of injection/config + strict mode: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)

---

## 6) Test coverage methodology

Tests should verify:

Implementation tests: [tests/stream_kernel/application_context/test_inject_decorator.py](../../../../tests/stream_kernel/application_context/test_inject_decorator.py), [tests/stream_kernel/integration/test_injection_integration.py](../../../../tests/stream_kernel/integration/test_injection_integration.py), [tests/stream_kernel/integration/test_config_injection_integration.py](../../../../tests/stream_kernel/integration/test_config_injection_integration.py), [tests/stream_kernel/integration/test_config_slice_integration.py](../../../../tests/stream_kernel/integration/test_config_slice_integration.py)

1) `@inject` resolution
   - dependency found → injected
   - missing dependency → error in strict mode
   - resolution by `(port_type, data_type)` for type-aware injection

2) Config injection
   - ConfigField reads values from node slice by default
   - `global.*` reads from global scope
   - missing config value → error in strict mode
   - `global.nodes.*` is rejected in strict, allowed in non-strict

3) Strict mode override
   - runtime.strict=false allows missing values with warnings
   - non-strict mode yields default/None without crashing

---

## 7) Blocking IO note (short-term)

The current runner is synchronous and will block on injected adapters.
This is **temporary**: the runner is expected to be rewritten soon, and the
blocking model should be treated as a transitional, "museum" behavior.
