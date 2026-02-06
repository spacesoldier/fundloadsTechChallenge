# Runner migration plan (legacy → routing/execution) [checkpoint]

## Context
We are moving from the legacy `kernel.runner` pipeline execution to the new
routing + execution model (`RoutingPort`, `WorkQueue`, `ContextStore`, `SyncRunner`).

## Current gaps
- remove remaining documentation references to `kernel.runner`
- continue full-flow integration (Runner ↔ RoutingPort ↔ WorkQueue ↔ ContextStore ↔ DAG builder)

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
   - Use WorkQueue + RoutingPort + ContextStore.
5. **Rewrite tests** ✅
   - Legacy runner tests replaced/removed.
6. **Remove legacy runner** ✅
   - `kernel.runner` removed from runtime and codebase.

## Notes
- Routing policy stays in Router.
- Execution policy stays in Runner.
- ContextStore provides metadata view to nodes.
