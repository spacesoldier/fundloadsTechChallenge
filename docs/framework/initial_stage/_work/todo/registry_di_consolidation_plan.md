# Registry DI consolidation plan

## Scope

This note tracks migration of framework registries to the same DI rails used by
business nodes/services.

## Registry inventory (current framework)

Build-time registries (assembly phase):

- `AdapterRegistry` (`stream_kernel.adapters.registry`)
- `InjectionRegistry` (`stream_kernel.application_context.injection_registry`)
- discovery maps (`discover_nodes`, `discover_adapters`, `discover_services`)

Runtime/service registries (execution phase):

- `ConsumerRegistry` (`stream_kernel.integration.consumer_registry`)

## Problem statement

Historically, runtime-level registry objects were manually wired in helper
functions and pushed into concrete constructors.
This breaks framework consistency and leaks orchestration details.

## Target model

- Build-time registries stay internal to bootstrap (they assemble graph/runtime).
- Runtime registries are exposed via standard DI `service` bindings.
- Runtime components (`RoutingPort`, `SyncRunner`) resolve dependencies through
  `inject.*` markers only.
- No direct constructor injection of runtime registries from `runtime.py`.

## Changes implemented in this step

- `RoutingPort` now resolves `ConsumerRegistry` via `inject.service(ConsumerRegistry)`.
- `execution.builder` now provides explicit registry binding function:
  - `ensure_runtime_registry_bindings(...)` to register `service<ApplicationContext>`.
- `ConsumerRegistry` is now provided by discovered platform service
  `DiscoveryConsumerRegistry` (built from `ApplicationContext.nodes`), not by
  manual runtime instance wiring.
- runtime queue/topic/routing bindings are now discovery-driven service contracts
  (`InMemoryQueue`, `InMemoryTopic`, `RoutingPort`) and are no longer registered
  by a dedicated runner-binding helper in `execution.builder`.
- `run_with_sync_runner(...)` no longer accepts `consumer_registry`; runner
  startup is DI-only.
- `runtime.py` now binds runtime registries through builder API and runs SyncRunner
  without manual registry parameters.
- scenario-scoped resources are closed after run (`ScenarioScope.close()`),
  and observer lifecycle is finalized in the same runtime path.

## Next candidates

- replace remaining legacy documentation references implying constructor-level
  registry wiring.
- keep `stream_kernel.integration.__init__` import-light to avoid DI cycles.
- continue moving build-time maps to explicit typed contracts where needed
  (without re-introducing runtime constructor wiring).
