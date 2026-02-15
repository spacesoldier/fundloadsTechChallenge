# Phase J.1 — Async capability propagation (adapter -> service -> node)

## Goal

Make runner auto-selection depend on actual execution capability of injected components, not on port names:

- async adapter binding marks DI binding as async;
- service that injects async dependency becomes async-capable;
- node that injects async service becomes async-capable;
- planner picks AsyncRunner when async-capable dependency chain is present.

## TDD steps

1. RED: adapter capability metadata
- extend adapter contract tests to assert async execution metadata is discoverable.

2. RED: adapter wiring propagation
- when adapter metadata marks async execution, `InjectionRegistry` binding is registered with `is_async=True`.

3. RED: service-level propagation
- discovered service with async injected adapter is registered as async binding;
- transitive service dependency (`service -> service`) keeps async marker.

4. RED: planning propagation
- node with `inject.service(...)` resolves to async pool when service binding is async.

5. GREEN: implementation
- adapter metadata includes execution mode;
- runtime adapter binding code propagates `is_async`;
- service discovery registration computes async capability from injected dependencies.

6. Regression
- run adapter discovery/wiring, builder service registration, execution planning tests.

## Acceptance criteria

- no async decisions based on port type names alone;
- async capability is inferred from binding metadata across DI chain;
- runner auto-selection reacts to async-capable injected graph.

## Execution status

- [x] Step 1 complete
- [x] Step 2 complete
- [x] Step 3 complete
- [x] Step 4 complete
- [x] Step 5 complete
- [x] Step 6 complete

## Regression commands run

- `.venv/bin/pytest -q tests/stream_kernel/adapters/test_adapter_discovery.py tests/stream_kernel/adapters/test_adapter_wiring.py tests/stream_kernel/execution/runtime/test_execution_planning.py tests/stream_kernel/execution/orchestration/test_builder.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_child_bootstrap.py tests/stream_kernel/app/test_framework_consumer_registry.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/runtime`
