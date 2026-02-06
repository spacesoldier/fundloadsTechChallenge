# Runner migration plan (legacy → routing/execution)

## Context
We are moving from the legacy `kernel.runner` pipeline execution to the new
routing + execution model (`RoutingPort`, `WorkQueue`, `ContextStore`, `SyncRunner`).

## Current gaps
- runtime still uses legacy runner
- no ConsumerRegistry built from discovery
- no entry envelope wrapping (target + trace_id)
- SyncRunner expects targeted envelopes

## Target order of work

1. **Switch @node metadata** to `consumes/emits` (type tokens). ✅
   - Update tests first.
   - Update NodeMeta + decorators.
2. **Build ConsumerRegistry** from discovery output.
   - ApplicationContext or separate builder.
3. **Entry wrapping**
   - Adapters remain payload‑only.
   - Runner wraps payload into Envelope with `trace_id` + `target`.
4. **Wire runtime to SyncRunner**
   - Use WorkQueue + RoutingPort + ContextStore.
5. **Rewrite tests**
   - Replace legacy runner tests with new integration tests.
6. **Remove legacy runner**
   - Deprecate `kernel.runner` and remove from runtime.

## Notes
- Routing policy stays in Router.
- Execution policy stays in Runner.
- ContextStore provides metadata view to nodes.
