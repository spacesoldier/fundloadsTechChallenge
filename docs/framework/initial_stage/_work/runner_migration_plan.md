# Runner migration plan (legacy → routing/execution) [checkpoint]

## Context
We are moving from the legacy `kernel.runner` pipeline execution to the new
routing + execution model (`RoutingPort`, `WorkQueue`, KV context storage, `SyncRunner`).

## Current gaps
- remove remaining documentation references to `kernel.runner`
- complete DAG-aware full-flow runtime without sequence shim (`runtime.pipeline` fully removable)
- enforce contract safety for self-loop-like nodes (`consumes/emits` overlap)
- prepare static checks/guidelines in editor + CI before demo-project launch
- remove adapter factories from config/runtime and rely on discovery-based adapter registry
- lock framework-level adapter/port taxonomy for config stability
- migrate context storage from custom `ContextStore` to framework-native `kv` contract

## Target order of work

1. **Switch @node metadata** to `consumes/emits` (type tokens). ✅
   - Update tests first.
   - Update NodeMeta + decorators.
2. **Build ConsumerRegistry** from discovery output. ✅
   - ApplicationContext or separate builder.
3. **Entry wrapping** ✅
   - Adapters remain payload‑only.
   - Runner wraps payload into Envelope with `trace_id` + `target`.
4. **Wire runtime to SyncRunner** ✅
   - Use WorkQueue + RoutingPort + KV-backed context storage.
   - Bootstrap inputs through `RoutingPort` (token-based entry) instead of hardcoded first step.
5. **Rewrite tests** ✅
   - Legacy runner tests replaced/removed.
6. **Remove legacy runner** ✅
   - `kernel.runner` removed from runtime and codebase.
7. **Make pipeline optional** ✅
   - Runtime accepts missing `runtime.pipeline`.
   - No fallback to manual pipeline ordering.
8. **Stabilize routing during migration** ✅
   - Added self-loop protection in Router default fan-out (`source` exclusion).
   - Removed runtime next-step targeting shim.
9. **DAG-based execution order** ✅
   - Runtime builds scenario order from DAG execution plan (topological), not discovery order.
10. **Next**
   - Replace runtime shim with DAG-aware routing for ambiguous tokens.
   - Move source/sink adapters into graph-native node model.
   - Remove `factory` from adapter YAML and resolve adapters by discovered adapter name.
   - Keep adapter YAML model-free (no class/type strings).
   - Validate stable port types (`stream`, `kv_stream`, `kv`, `request`, `response`).
   - Replace project-level domain ports with service layer (`inject.service(...)`) over stable framework ports. ✅
   - WindowStore migrated to service contract (`WindowStoreService`) and wired via `service` binding. ✅
   - PrimeChecker migrated to service contract (`PrimeCheckerService`) and wired via `service` binding. ✅
   - Project `fund_load/ports` module removed from runtime codepath. ✅
   - WindowStore service now persists via framework `KVStore` backend (no project-port storage shim). ✅
   - Runner context persistence migration to `kv`-backed service facade completed (`ContextService` over `kv`).
   - Runtime default KV backend is validator-driven (`runtime.platform.kv.backend`, default `memory`) and auto-bound to `kv<KVStore>`. ✅
11. **Platform-ready safeguards**
   - Add contract lint: `consumes=[T]` + `emits=[T]` must be explicit by policy.
   - Policy options:
     - split token by stage (`T_in` -> `T_out`), or
     - explicit output target contract.
   - Surface violations in tooling (pyright/mypy-compatible diagnostics + CI).
12. **Final migration target**
   - Remove `runtime.pipeline` fully. ✅
   - Keep routing-only startup and DAG preflight as the single execution path.
   - Run test/demo project on framework runtime with no legacy sequencing fallback.

## Notes
- Routing policy stays in Router.
- Execution policy stays in Runner.
- KV-backed context storage provides metadata view to nodes.
- Runtime executes with DAG-based plan and routing; no next-step chaining shim remains.
- Default fan-out must not self-loop; explicit self-target remains an intentional advanced mode.
- Adapter config should describe runtime-facing knobs only (`settings/binds`).
- Adapter type/model mapping belongs to code metadata (`@adapter` + helper mapping).
