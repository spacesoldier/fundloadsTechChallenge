# Auto-discovery policy (annotation-driven)

This document defines how the framework discovers nodes and adapters
**without hardcoded package names**. The policy mirrors a Spring Boot–like
"scan the app and react to annotations" approach.

---

See also: [Application context and discovery](./Application%20context%20and%20discovery.md), [Node and stage specifications](./Node%20and%20stage%20specifications.md), [Factory and injection model](./Factory%20and%20injection%20model.md), [Injection and strict mode](./Injection%20and%20strict%20mode.md), [Injection registry](./Injection%20registry.md)

---

## 1) Root package

The framework identifies the **root package** of the application
(e.g., `example_app`) using the entrypoint module or project metadata.

If the root package cannot be determined, startup fails with an explicit error.

---

## 2) Scan scope

The framework scans **all modules under the root package**:

- `pkgutil.walk_packages` under the root package path
- import each module
- inspect for annotation metadata (`@node`, `@adapter`, `@stage`)

There are **no hardcoded subpackage names**. The structure is free-form.

---

## 3) Inclusion rule

A module participates in the context if it defines at least one annotated
symbol:

- `@node`
- `@adapter`
- `@stage`

Modules without annotations are ignored.

---

## 4) Exclusion and overrides (optional)

To keep large codebases manageable, the framework may support
optional overrides:

- `APP_CONTEXT_ROOT` to explicitly set the root package
- `APP_CONTEXT_EXCLUDE` (comma-separated module prefixes, exact match or prefix)

These are **optional** and not required for initial adoption.

---

## 5) Determinism

- Discovery order is deterministic within a single run.
- Node identity is based on explicit `name` metadata; duplicates are errors.

---

## 6) Implementation references

- Auto-discovery entrypoint: [src/stream_kernel/application_context/application_context.py](../../../../src/stream_kernel/application_context/application_context.py)
- Discovery walk/import: [src/stream_kernel/kernel/discovery.py](../../../../src/stream_kernel/kernel/discovery.py)
- Node/stage metadata: [src/stream_kernel/kernel/node.py](../../../../src/stream_kernel/kernel/node.py), [src/stream_kernel/kernel/stage.py](../../../../src/stream_kernel/kernel/stage.py)

---

## 7) Test coverage methodology

The discovery behavior must be covered by tests that validate:

Implementation tests: [tests/stream_kernel/application_context/test_auto_discovery.py](../../../../tests/stream_kernel/application_context/test_auto_discovery.py), [tests/stream_kernel/discovery/test_discovery.py](../../../../tests/stream_kernel/discovery/test_discovery.py), [tests/stream_kernel/discovery/test_node_decorator.py](../../../../tests/stream_kernel/discovery/test_node_decorator.py)

1) **Root detection**
   - failure when root package is missing or misconfigured.

2) **Module scan**
   - modules under root are scanned (no hardcoded subpackages).
   - modules without annotations are ignored.

3) **Annotation handling**
   - `@node` / `@adapter` / `@stage` are discovered.
   - duplicate node names are rejected.

4) **Overrides**
   - `APP_CONTEXT_ROOT` and `APP_CONTEXT_EXCLUDE` (if supported) alter scan scope.

These tests should be isolated and not depend on project-specific modules.
