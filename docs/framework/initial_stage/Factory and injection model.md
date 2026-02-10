# Factory and injection model (summary)

This document consolidates the agreed model for nodes, factories, injection,
and strict mode based on the 8-point discussion and follow-up clarifications.

---

## 1) What a `@node` can be

- **Class node**: a class with `__call__` (or `_call` if we later normalize).
- **Function node**: a plain callable marked with `@node`.
  - Function nodes **may be factories** (see §2.1), returning a step callable configured for the scenario.

Discovery collects both. Instantiation happens **after discovery**.

---

## 2) Factory responsibility

Factories live in the **framework**:

- discovery finds the node declarations,
- the framework instantiates them per scenario,
- no manual wiring per step in the project.

### 2.1 Function factories (Spring-style)

When a `@node` is attached to a **function**, the framework may treat it as a **factory**:

- **Factory contract**: `(cfg: dict[str, object]) -> step`
- The factory is called **once per scenario** (build time),
- The returned `step` is used for the whole stream (run time).

This allows "Spring-style" bean factories without introducing new decorators:
the framework decides at build time whether a function is a step or a factory.

**Important**: dependency injection is handled separately via `@inject` (see §4),
so factories receive **config only**, not wiring.

**Resolution rule (current):**

- The framework **tries to call** the function with `cfg`.
- If that call raises `TypeError`, the function is treated as a **plain step**.
- If the call succeeds, the return value **must be callable** (or it is an error).

---

## 3) Config injection

Default approach: **descriptor-based config fields**.

```python
class ComputeFeatures:
    monday_multiplier = ConfigField(
        \"features.monday_multiplier.multiplier\",
        default=Decimal(\"1.0\")
    )
```

Values are injected from the **app config slice** at build time.

### 3.1 Config slice resolution

- Each node receives `nodes.<node_name>` as its default scope.
- `config.value("x.y")` reads from the node slice, then from `global`.
- `config.value("global.x.y")` reads only from the global scope.
- In strict mode, `global.nodes.<other>.*` is rejected.
- In non-strict mode, `global.nodes.<other>.*` is allowed as a compatibility escape hatch.

See: [Injection and strict mode](Injection%20and%20strict%20mode.md), [Injection registry](Injection%20registry.md)

---

## 4) Dependency injection (`@inject`)

Dependencies are resolved by name from application context wiring:

- `state_store`
- `feature_checker`
- `output_sink`

Missing dependencies are handled by **strict mode** (default: error).

---

## 5) Strict mode

Strict mode is **enabled by default**.

If enabled:

- missing config values → error
- missing dependencies → error

If disabled:

- missing values → warning + default/`None`

Config override:

```yaml
runtime:
  strict: false
```

---

## 6) Function nodes and wrappers

Function nodes are wrapped into a lightweight object to normalize lifecycle:

- uniform instantiation per scenario
- controlled injection hooks

This prevents hidden state from leaking across scenarios.

---

## 7) Instantiation scope

Nodes are **instantiated per scenario**, not globally:

- the same node may be instantiated multiple times with different config
- a node instance is not shared across scenarios

---

## 8) Dependency validation

ApplicationContext validates the node graph at startup:

- checks missing dependencies
- checks duplicate node names

Failure behavior depends on strict mode.

---

## 9) Implementation references

- Context assembly + injection apply: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)
- Injection registry + scope: [src/stream_kernel/application_context/injection_registry.py](../../../../src/stream_kernel/application_context/injection_registry.py)
- Injection descriptors: [src/stream_kernel/application_context/inject.py](../../../../src/stream_kernel/application_context/inject.py)
- Config descriptors: [src/stream_kernel/application_context/config_inject.py](../../../../src/stream_kernel/application_context/config_inject.py)
- Discovery + runtime artifact builder: [src/stream_kernel/kernel/discovery.py](../../../../src/stream_kernel/kernel/discovery.py), [src/stream_kernel/execution/builder.py](../../../../src/stream_kernel/execution/builder.py)

---

## 10) Testing strategy

- Unit tests for `@node`, `@inject`, config fields.
- Integration tests for discovery + scenario build.
- E2E tests for multiple scenarios (overlapping and disjoint node sets).
- Function-node wrapping should be covered by unit tests.

Implementation tests (representative): [tests/stream_kernel/discovery/test_node_decorator.py](../../../../tests/stream_kernel/discovery/test_node_decorator.py), [tests/stream_kernel/application_context/test_inject_decorator.py](../../../../tests/stream_kernel/application_context/test_inject_decorator.py), [tests/stream_kernel/application_context/test_application_context.py](../../../../tests/stream_kernel/application_context/test_application_context.py), [tests/stream_kernel/application_context/test_factory_nodes.py](../../../../tests/stream_kernel/application_context/test_factory_nodes.py), [tests/stream_kernel/integration/test_injection_integration.py](../../../../tests/stream_kernel/integration/test_injection_integration.py)
