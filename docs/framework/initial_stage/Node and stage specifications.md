# Node and stage specifications (initial stage)

This document defines the **node** and **stage** concepts used by the
ApplicationContext discovery model.

---

See also: [Application context and discovery](./Application%20context%20and%20discovery.md), [Scenario vs node axes](./Scenario%20vs%20node%20axes.md), [Factory and injection model](./Factory%20and%20injection%20model.md), [Injection and strict mode](./Injection%20and%20strict%20mode.md)

---

## 1) Node

A **node** is a discoverable, callable processing unit.

### 1.1 Contract

- Callable: `(msg, ctx) -> Iterable[msg]`
- Side effects are allowed only if the node is explicitly an IO node.
- Node identity is defined by a **unique name**.

### 1.2 Metadata (NodeMeta)

Required:

- `name: str` (non-empty, unique)

Optional:

- `stage: str` (default is inferred if not provided)
- `requires: list[str]` (names of upstream nodes)
- `provides: list[str]` (names/tokens of outputs)

Duplicates in `requires` or `provides` are invalid.

### 1.3 Decorator

Nodes are declared with a decorator:

```
@node(name="compute_time_keys", stage="time", requires=["parse"])
class ComputeTimeKeys:
    def __call__(self, msg, ctx): ...
```

The decorator attaches `__node_meta__` to the class/function.

---

## 2) Stage

A **stage** is a **label** used for grouping nodes.

### 2.1 Purpose

- readability
- diagnostics and tracing
- future deployment partitioning

Stages **do not** impose ordering.

### 2.2 Stage as an entity

Stages are first-class entities (`StageDef`) that group `NodeDef` instances.
This makes it possible to treat a stage as a potential **deployment unit**
in the future (horizontal slicing), even though current execution is
single-process.

### 2.3 Defaults and overrides

If `stage` is not explicitly set on a node, it is **inferred** from the
declaring symbol name (class or function name). This avoids an empty stage
and keeps grouping meaningful.

Stages may be overridden by configuration:

- default stage = `NodeMeta.stage`
- override by node name (config map)

Example (conceptual):

```
stages:
  overrides:
    compute_time_keys: time
    evaluate_rules: policy
```

---

## 3) Discovery rules

- only objects with `__node_meta__` are considered nodes
- name collisions are errors
- discovery order is preserved for deterministic grouping

---

## 4) Instantiation rules (initial)

- nodes are **instantiated per scenario**
- no global singleton assumption
- lifecycle beyond per-scenario is out of scope for this stage

---

## 5) Implementation references

- Node metadata and decorator: [src/stream_kernel/kernel/node.py](../../../../src/stream_kernel/kernel/node.py)
- Stage entity and decorator: [src/stream_kernel/kernel/stage.py](../../../../src/stream_kernel/kernel/stage.py)
- Discovery and grouping: [src/stream_kernel/kernel/discovery.py](../../../../src/stream_kernel/kernel/discovery.py), [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)

---

## 6) Test coverage methodology

Tests should verify:

Implementation tests: [tests/stream_kernel/discovery/test_node_decorator.py](../../../../tests/stream_kernel/discovery/test_node_decorator.py), [tests/stream_kernel/discovery/test_discovery.py](../../../../tests/stream_kernel/discovery/test_discovery.py), [tests/stream_kernel/integration/test_stage_method_nodes.py](../../../../tests/stream_kernel/integration/test_stage_method_nodes.py)

1) **Node metadata**
   - `@node` attaches `NodeMeta`.
   - invalid metadata (empty name, duplicates) fails fast.

2) **Stage grouping**
   - default stage inference works (symbol name).
   - stage overrides from config are applied.

3) **Instantiation model**
   - nodes are instantiated per scenario (no global singleton behavior).
