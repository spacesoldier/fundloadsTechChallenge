# Injection registry (scenario-scoped)

This document defines the **injection registry** used to resolve ports/adapters
by `(port_type, data_type)` and to create **per-scenario instances**.

---

See also: [Injection and strict mode](./Injection%20and%20strict%20mode.md), [Factory and injection model](./Factory%20and%20injection%20model.md), [Application context and discovery](./Application%20context%20and%20discovery.md)

---

## 1) Purpose

- Centralize bindings for injectable ports/adapters.
- Resolve dependencies by **port type** + **data type**.
- Ensure **scenario isolation** (no cross-vertical state leaks).

---

## 2) Registry model

The registry holds **factories**, not instances:

- key: `(port_type, data_type)`
- value: `factory() -> instance`

At scenario build time, the registry creates a **ScenarioScope**:

```
scope = registry.instantiate_for_scenario("baseline")
store = scope.resolve("kv", UserState)
```

Each scenario receives a fresh set of instances.

---

## 3) Errors and strict mode

Errors are explicit:

- duplicate binding → `InjectionRegistryError`
- missing binding → `InjectionRegistryError` (or warning if strict mode is off)

Strict mode remains **enabled by default**.

---

## 4) Implementation references

- Injection registry + scope: [src/stream_kernel/application_context/injection_registry.py](../../../../src/stream_kernel/application_context/injection_registry.py)
- Injection descriptors (caller side): [src/stream_kernel/application_context/inject.py](../../../../src/stream_kernel/application_context/inject.py)
- Application context usage: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)

---

## 5) Test coverage methodology

Tests should verify:

Implementation tests: [tests/stream_kernel/application_context/test_injection_registry.py](../../../../tests/stream_kernel/application_context/test_injection_registry.py), [tests/stream_kernel/integration/test_injection_integration.py](../../../../tests/stream_kernel/integration/test_injection_integration.py)

1) Resolution by `(port_type, data_type)`
2) Isolation across scenarios (distinct instances)
3) Missing binding errors
4) Duplicate binding errors
5) Non-strict mode fallback (warning + None) when configured
