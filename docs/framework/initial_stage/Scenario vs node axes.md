# Scenario vs node axes (deployment and instantiation model)

This document clarifies the **two orthogonal axes** of the system:

- **Node/Stage axis**: horizontal decomposition by logical processing steps.
- **Scenario axis**: vertical composition of nodes into concrete pipelines.

This separation becomes important when we introduce discovery and
ApplicationContext-driven wiring.

---

See also: [Node and stage specifications](./Node%20and%20stage%20specifications.md), [Application context and discovery](./Application%20context%20and%20discovery.md), [Factory and injection model](./Factory%20and%20injection%20model.md), [Injection and strict mode](./Injection%20and%20strict%20mode.md), [Injection registry](./Injection%20registry.md)

---

## 1) Node/Stage axis (horizontal)

Nodes represent **reusable processing units**:

- `@node(name="parse_input", stage="parse")`
- `@node(name="compute_time_keys", stage="time")`
- `@node(name="evaluate_policies", stage="policies")`

Stages are **logical groupings** (for readability/trace/diagnostics), not
ordering rules. A stage can be used as a filter or label for deployment
targeting in the future, but does not impose execution order by itself.

**Key properties:**

- Nodes are discoverable and reusable.
- Stages are descriptive, not structural.

---

## 2) Scenario axis (vertical)

Scenarios are **concrete pipelines**:

- `baseline`
- `experimental`

Each scenario selects:

- which nodes participate,
- in which order (explicit or derived),
- with which configuration.

**Key properties:**

- A node can appear in multiple scenarios.
- Each scenario can instantiate the same node with different parameters.

---

## 3) Instantiation model

When using ApplicationContext:

- **Discovery** collects node definitions (metadata + class/function).
- **Scenario build** instantiates nodes **per scenario**.

Therefore, node instances are **not singletons globally**; they are scoped
to a scenario build. This allows:

- multiple parameterizations of the same node,
- multiple scenarios built from the same node set.

---

## 4) Future deployment implications

Because nodes and scenarios are orthogonal:

- we can later split deployment by **stage** (horizontal slice),
- or by **scenario** (vertical slice),
- or by a combination (e.g., shared parse stage + scenario-specific policies).

For now, this is conceptual only; execution remains within a single process.

---

## 5) Implementation references

- Scenario model: [src/stream_kernel/kernel/scenario.py](../../../../src/stream_kernel/kernel/scenario.py)
- Scenario build path: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)
- Application context instantiation: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)
- Node/stage metadata: [src/stream_kernel/kernel/node.py](../../../../src/stream_kernel/kernel/node.py), [src/stream_kernel/kernel/stage.py](../../../../src/stream_kernel/kernel/stage.py)

---

## 6) Test coverage methodology

Tests should validate:

Implementation tests: [tests/stream_kernel/integration/test_injection_integration.py](../../../../tests/stream_kernel/integration/test_injection_integration.py), [tests/stream_kernel/application_context/test_application_context_scenario.py](../../../../tests/stream_kernel/application_context/test_application_context_scenario.py)

1) **Scenario composition**
   - a scenario is built as an ordered list of nodes.
   - the same node can appear in multiple scenarios.

2) **Instantiation scope**
   - node instances are scoped to scenario builds.
   - stage grouping does not affect execution order.
