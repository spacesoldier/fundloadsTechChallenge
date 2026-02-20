# Execution runner engine unification (multiprocess boundary path) — TDD plan

## Why this plan exists

Current multiprocess child execution still has a dedicated boundary loop path that partially
duplicates runner behavior. This creates drift between:

- single-process execution (runner-driven);
- multiprocess boundary execution (custom loop with compatibility branches).

Recent fixes restored async node handling and dependency-based auto profile selection,
but the runtime still needs full engine unification for long-term consistency.

## Scope

In scope:

- unify boundary execution semantics with runner semantics;
- make async/sync behavior deterministic per process-group dependency profile;
- keep observability dispatch on framework rails without recursion amplification;
- preserve reply/trace continuity across process boundaries.

Out of scope (separate plans):

- FastAPI ingress implementation details;
- Redis bus ownership migration;
- deep perf tuning beyond configuration-safe defaults.

## Baseline state (as of this plan start)

- `child_bootstrap` supports async node outputs and async-aware observer callbacks.
- process-group `runner_profile_effective` is now inferred per group, not globally.
- source/sink adapter wrappers propagate async capability to pool planning.
- multiprocess worker execution still goes through custom boundary loop.

## TDD phases

### Phase A — contract freeze and characterization (RED)

Goal: freeze current public contracts before refactor.

Add/refresh tests:

- `RUN-UNI-A1`: boundary execution returns deterministic `Envelope` list order.
- `RUN-UNI-A2`: per-group `runner_profile_effective` remains stable for mixed sync/async topology.
- `RUN-UNI-A3`: async node in child path remains supported.

Exit criteria:

- tests describe current behavior without changing contracts.

### Phase B — runner external channels (RED → GREEN)

Goal: extend runner with explicit boundary-safe channels.

Add tests:

- `RUN-UNI-B1`: runner can collect terminal outputs as envelopes.
- `RUN-UNI-B2`: runner can emit external deliveries for targets outside local node set
  when boundary mode is enabled.
- `RUN-UNI-B3`: strict default behavior remains unchanged when boundary mode is disabled.

Implementation:

- add explicit boundary-mode switches/collectors in runner engine;
- keep default path backward-compatible.

Exit criteria:

- runner can execute local nodes and return cross-group outputs without crashing.

Status:

- complete (implemented).
- tests added:
  - `RUN-UNI-B1` `test_sync_runner_collects_terminal_outputs_in_boundary_mode`
  - `RUN-UNI-B2` `test_sync_runner_collects_external_deliveries_for_unknown_local_targets`
  - `RUN-UNI-B3` `test_sync_runner_default_mode_still_fails_on_unknown_targets`
  - `RUN-UNI-B4` `test_sync_runner_boundary_mode_treats_no_consumer_output_as_terminal`
  - `RUN-UNI-B5` `test_async_runner_collects_external_deliveries_for_unknown_local_targets`
  - `RUN-UNI-B6` `test_sync_runner_boundary_mode_collects_explicit_targeted_envelope_outputs`
- implementation notes:
  - `SyncRunner` and `AsyncRunner` now support optional boundary collectors:
    - `allow_external_deliveries`
    - `external_deliveries`
    - `terminal_outputs`
  - strict default semantics remain unchanged when boundary mode is disabled.

### Phase C — switch child boundary execution to runner engine (RED → GREEN)

Goal: replace step-by-step custom boundary execution loop with runner-based execution.

Add tests:

- `RUN-UNI-C1`: child boundary path uses runner channels for terminal/external outputs.
- `RUN-UNI-C2`: reply/trace/span continuity remains intact.
- `RUN-UNI-C3`: observability callbacks still fire once per node execution.

Implementation:

- route boundary input envelopes through runner queue;
- map runner external/terminal outputs back to boundary response payload.

Exit criteria:

- child boundary execution no longer duplicates node invocation/routing logic.

Status:

- complete (implemented).
- tests added:
  - `RUN-UNI-C1`
    `test_child_boundary_loop_executes_local_chain_inside_group_before_emitting`
  - `RUN-UNI-C2`
    `test_child_boundary_loop_preserves_trace_reply_and_span_through_local_chain`
  - `RUN-UNI-C3`
    `test_child_boundary_loop_observability_callbacks_fire_once_per_executed_node`
- implementation notes:
  - `execute_child_boundary_loop(...)` now delegates execution to `SyncRunner` / `AsyncRunner`
    with boundary collectors enabled.
  - boundary-mode observability context (`__process_group`, `__handoff_from`, `__route_hop`)
    is injected through runner `observability_context_enricher`.
  - execution mode split:
    - when `runtime.platform.process_groups` maps current group, local chain is executed inside worker;
    - when group mapping is absent, compatibility one-hop mode remains (explicit targets only).
  - async node compatibility preserved via effective profile + coroutine-function fallback detection.

### Phase D — observability recursion guard hardening (RED → GREEN)

Goal: guarantee no self-amplifying observability traffic.

Add tests:

- `RUN-UNI-D1`: observability/system outputs do not recursively generate infinite dispatch;
- `RUN-UNI-D2`: service/system node exclusions remain deterministic in both sync/async paths.

Implementation:

- keep guard semantics centralized in runner/observer interfaces;
- remove obsolete compatibility branches in boundary path.

Exit criteria:

- no recursion leaks with tracing/logging exporters enabled.

Status:

- complete (implemented).
- tests added:
  - `RUN-UNI-D1`
    `test_runner_guard_prevents_observability_self_dispatch_recursion_on_system_nodes`
  - `RUN-UNI-D2`
    `test_system_observability_node_exclusion_is_deterministic_for_sync_and_async`
- implementation notes:
  - centralized guard is now enforced in runner engine:
    `system.obs.*` nodes are executed without observability callbacks
    (`before_node` / `after_node` / `on_node_error`) in both sync and async paths.
  - recursion protection no longer depends only on observer-specific behavior.

### Phase E — cleanup + docs sync (REFACTOR)

Goal: remove legacy seams and align docs/tests.

Tasks:

- remove dead boundary helper code replaced by runner path;
- update execution/runtime docs and relevant `_work` links;
- ensure topology debug logs explain requested/effective runner profile per group.

Exit criteria:

- no duplicate execution logic remains between runner and boundary path.

Status:

- complete (implemented).
- cleanup notes:
  - removed obsolete pre-unification helper path from
    `child_bootstrap` (manual async/observability boundary helpers no longer used after runner delegation).
  - removed unused generic observability dispatch wrapper from
    `observability_system_nodes` (concrete `@node` dispatch classes remain the only active path).
  - runner-level recursion guard is now documented as engine invariant (`system.obs.*` trace-silent execution).

## Execution order

1. Phase A
2. Phase B
3. Phase C
4. Phase D
5. Phase E

## Progress log

- [x] Pre-plan baseline fix: async node output support in child boundary path.
- [x] Pre-plan baseline fix: group-scoped auto runner profile inference.
- [ ] Phase A
- [x] Phase B
- [x] Phase C
- [x] Phase D
- [x] Phase E

## Linked plans

- `docs/framework/initial_stage/_work/observability_phaseL_runner_dispatch_and_recursion_guard_tdd_plan.md`
- `docs/framework/initial_stage/_work/observability_platform_rails_no_hardcode_tdd_plan.md`
- `docs/framework/initial_stage/_work/web_phase5pre_handoff_otlp_jaeger_tdd_plan.md`
- `docs/framework/initial_stage/_work/web_multiprocessing_secure_tcp_fastapi_plan.md`
