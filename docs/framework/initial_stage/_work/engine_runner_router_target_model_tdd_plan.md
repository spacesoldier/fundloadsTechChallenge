# Engine target model and migration plan (runner/router/reply)

## Purpose

Define and implement a clean target model for runtime engine responsibilities
after Phase 4bis completion.

Context:

- review findings: [engine_review_runner_router_findings](engine_review_runner_router_findings.md)
- current web track master plan:
  [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- follow-up cleanup wave:
  [engine_runtime_contract_cleanup_tdd_plan](engine_runtime_contract_cleanup_tdd_plan.md)

---

## Current state (delta to target)

1. `SyncRunner` still handles reply waiter registration/completion directly.
2. Boundary dispatch still applies one global `dispatch_group` for a batch.
3. Child execution path is not equivalent to parent runner invocation model.
4. Execution policy still depends on hardcoded runtime conventions (`execution.cpu`, `sink:*`).

---

## Target model (contract-level)

### 1) Runner = execution only

Runner responsibilities:

- pop message from queue;
- load context;
- invoke node;
- pass outputs to routing pipeline;
- enqueue local deliveries.

Runner does **not**:

- own request/reply correlation policy;
- own waiter timeout policy;
- infer placement policy.

### 2) Router = routing decisions only

Router receives outputs and returns structured routing result:

- `local_deliveries` (`target`, `payload`, metadata);
- `boundary_deliveries` grouped by process group;
- `terminal_events` eligible for correlation.

### 3) ReplyCoordinatorService = correlation only

Dedicated service contract:

- `register_if_requested(trace_id, reply_to, timeout)`
- `complete_if_waiting(trace_id, terminal_event)`
- `expire/cancel/poll/in_flight`

Performance rule:

- no per-hop reply checks;
- O(1) lookup only on ingress registration and terminal completion.

### 4) Boundary dispatcher = placement-driven

Cross-process routing must be built from execution placement map:

- `target node -> process_group`
- grouped boundary batches per target group
- same-group path stays local

### 5) Parent/child execution parity

Child runtime executes nodes through the same invocation contract as parent:

- same DI/scenario scope rules
- same context metadata loading model
- same observability boundaries

---

## TDD migration sequence

### Step A — contract freeze (RED)

- Add contract tests for target split:
  - `ENG-REPLY-01` runner does not call waiter API directly.
  - `ENG-ROUTE-01` router returns structured result (local/boundary/terminal).
  - `ENG-PLACEMENT-01` boundary batches are per target-group, not global.
  - `ENG-CHILD-01` child invocation path uses DI/context parity contract.

Status:

- [x] Completed on February 12, 2026 (RED confirmed).
- Added: `tests/stream_kernel/execution/test_engine_target_model_contract.py`
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_engine_target_model_contract.py`
- Current RED failures (expected for Step A):
  - `ENG-REPLY-01`: `SyncRunner` still has direct reply waiter fields.
  - `ENG-REPLY-02`: terminal path still calls waiter logic in runner hot path.
  - `ENG-REPLY-03`: ingress `reply_to` without explicit target is not registered.
  - `ENG-ROUTE-01`: router returns raw list instead of structured routing result.
  - `ENG-PLACEMENT-01`: boundary dispatch still uses one global dispatch group.
  - `ENG-CHILD-01`: child boundary execution bypasses DI parity.

### Step B — reply extraction (GREEN)

- Introduce `ReplyCoordinatorService` and wire it in runtime.
- Move registration/completion from runner to coordinator integration points.
- Keep Phase 4 behavior parity.

Status:

- [x] Completed on February 13, 2026 (GREEN for reply split).
- Added:
  - `src/stream_kernel/platform/services/reply_coordinator.py`
  - `tests/stream_kernel/execution/test_reply_coordinator.py`
- Updated:
  - `src/stream_kernel/execution/runner.py` (runner now uses `ReplyCoordinatorService`)
  - `src/stream_kernel/execution/lifecycle_orchestration.py` (boundary terminal completion uses coordinator)
  - `src/stream_kernel/execution/builder.py` (transitional fallback to legacy waiter when coordinator is not bound)
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_reply_coordinator.py tests/stream_kernel/execution/test_runner_reply_correlation.py tests/stream_kernel/execution/test_runner_interface.py tests/stream_kernel/execution/test_engine_target_model_contract.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_builder.py tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_runner_routing_integration.py tests/stream_kernel/execution/test_runner_context_integration.py tests/stream_kernel/execution/test_runner_observers.py`
  - `.venv/bin/pytest -q tests/stream_kernel/app/test_framework_run.py tests/stream_kernel/integration/test_full_flow_dag_runner_integration.py tests/stream_kernel/execution/test_child_bootstrap.py`
- Remaining RED tests in `test_engine_target_model_contract.py` (for next steps):
  - `ENG-ROUTE-01`
  - `ENG-PLACEMENT-01`
  - `ENG-CHILD-01`

### Step C — router result model (GREEN)

- Replace `list[(target,payload)]` with routing result DTO.
- Keep compatibility adapter in `RoutingPort` for migration period.
- Update runner to consume new result contract.

Status:

- [x] Completed on February 13, 2026 (GREEN for routing result contract).
- Added/updated:
  - `src/stream_kernel/routing/router.py`:
    - introduced `RoutingResult` (`local_deliveries`, `boundary_deliveries`, `terminal_outputs`)
    - `Router.route(...)` now returns `RoutingResult`
    - compatibility helpers (`__iter__`, `__eq__`, `as_pairs`)
  - `src/stream_kernel/integration/routing_port.py`:
    - `route(...) -> RoutingResult`
    - added transitional `route_pairs(...) -> list[(target, payload)]`
  - `src/stream_kernel/execution/runner.py`:
    - switched to explicit `routing_result.local_deliveries`
  - `tests/stream_kernel/integration/test_routing_port.py`:
    - added structured-result contract coverage (`route()` + `route_pairs()`).
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/routing/test_router_routing.py tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/execution/test_engine_target_model_contract.py`
  - `.venv/bin/pytest -q tests/stream_kernel/routing/test_router_routing.py tests/stream_kernel/integration/test_routing_port.py tests/stream_kernel/execution/test_runner_routing_integration.py tests/stream_kernel/execution/test_runner_interface.py tests/stream_kernel/execution/test_engine_target_model_contract.py`
- Remaining RED tests in `test_engine_target_model_contract.py` (next steps):
  - `ENG-PLACEMENT-01`
  - `ENG-CHILD-01`

### Step D — placement-driven boundary dispatch (GREEN)

- Replace `_select_dispatch_group` batch-global policy with per-target-group batching.
- Add diagnostics for unknown placement mapping at preflight/runtime boundary.

Status:

- [x] Completed on February 13, 2026 (GREEN for placement dispatch grouping).
- Updated:
  - `src/stream_kernel/execution/lifecycle_orchestration.py`
    - `_build_boundary_dispatch_inputs(...)` now resolves `dispatch_group` per envelope target
      via `runtime.platform.process_groups[].nodes`
    - added deterministic target-placement validation for explicit placement maps
    - kept legacy default dispatch-group fallback when no explicit placement map is declared
- Updated tests:
  - `tests/stream_kernel/execution/test_engine_target_model_contract.py`
    - `ENG-PLACEMENT-01` (per-target-group dispatch)
    - `ENG-PLACEMENT-03` (missing mapping fails deterministically)
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_engine_target_model_contract.py tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_builder.py`
- Remaining RED in engine contract suite (next step):
  - `ENG-CHILD-01`

### Step E — child parity refactor (REFACTOR)

- Remove direct `step(payload,{})` style from child boundary execution.
- Reuse runner-equivalent invocation pipeline in child runtime scope.
- Ensure context + observability parity.

Status:

- [x] Completed on February 13, 2026 (REFACTOR done).
- Updated:
  - `src/stream_kernel/execution/child_bootstrap.py`
    - child runtime now builds executable steps via `ApplicationContext.build_scenario(...)`
      (DI/scenario-scope parity)
    - replaced direct `step(payload,{})` execution with runner-equivalent invocation flow:
      - context metadata load from `ContextService`
      - service-node full-context rule parity
      - observability hooks (`before_node` / `after_node` / `on_node_error` / `on_run_end`)
    - preserved emitted-envelope normalization for boundary return path.
  - `src/stream_kernel/execution/runner.py`
    - added transitional compatibility adapter for route results (`RoutingResult` vs legacy list pairs).
- Updated tests:
  - `tests/stream_kernel/execution/test_engine_target_model_contract.py`
    - `ENG-CHILD-01` DI parity (green)
    - `ENG-CHILD-02` context metadata parity (green)
    - `ENG-CHILD-03` observability callback parity (green)
- Verified via:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_runner_context_integration.py tests/stream_kernel/execution/test_engine_target_model_contract.py tests/stream_kernel/execution/test_child_bootstrap.py tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_builder.py tests/stream_kernel/execution/test_runner_interface.py`

### Step F — regression/parity gate

- Re-run handoff/reply/process-supervisor suites.
- Re-run integration deterministic suites.
- Re-check memory profile parity with `jq -cS` + `diff -u`.

Status:

- [x] Completed on February 13, 2026 (regression/parity gate passed).
- Verified execution suites:
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_remote_handoff_contract.py tests/stream_kernel/execution/test_reply_waiter_contract.py tests/stream_kernel/execution/test_runner_reply_correlation.py tests/stream_kernel/execution/test_builder.py`
  - `.venv/bin/pytest -q tests/stream_kernel/execution/test_engine_target_model_contract.py tests/stream_kernel/execution/test_child_bootstrap.py tests/stream_kernel/execution/test_bootstrap_keys.py tests/stream_kernel/execution/test_reply_coordinator.py`
- Verified deterministic integration suites:
  - `.venv/bin/pytest -q tests/integration/test_end_to_end_baseline_limits.py tests/integration/test_end_to_end_experiment_features.py`
- Verified memory-profile parity:
  - baseline:
    - `PYTHONPATH=src .venv/bin/python -m fund_load --config src/fund_load/baseline_config_newgen.yml --input docs/analysis/data/assets/input.txt --output /tmp/engine_stepf_baseline_output.txt`
    - `jq -cS . /tmp/engine_stepf_baseline_output.txt > /tmp/engine_stepf_baseline_out.norm`
    - `jq -cS . docs/analysis/data/assets/output.txt > /tmp/engine_stepf_baseline_ref.norm`
    - `diff -u /tmp/engine_stepf_baseline_ref.norm /tmp/engine_stepf_baseline_out.norm`
  - experiment:
    - `PYTHONPATH=src .venv/bin/python -m fund_load --config src/fund_load/experiment_config_newgen.yml --input docs/analysis/data/assets/input.txt --output /tmp/engine_stepf_experiment_output.txt`
    - `jq -cS . /tmp/engine_stepf_experiment_output.txt > /tmp/engine_stepf_experiment_out.norm`
    - `jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/engine_stepf_experiment_ref.norm`
    - `diff -u /tmp/engine_stepf_experiment_ref.norm /tmp/engine_stepf_experiment_out.norm`
- Result:
  - all listed suites are green;
  - both `diff -u` comparisons are empty (exact normalized parity).

---

## Detailed test cases

### Reply/coordinator split

- `ENG-REPLY-01` runner has no direct `register/complete` waiter calls.
- `ENG-REPLY-02` terminal completion path works through coordinator only.
- `ENG-REPLY-03` envelope with `reply_to` and no explicit `target` still registers correlation once.

### Routing result contract

- `ENG-ROUTE-01` local fan-out returns only local deliveries.
- `ENG-ROUTE-02` boundary targets are emitted as grouped boundary batches.
- `ENG-ROUTE-03` terminal outputs are separated from delivery outputs.

### Placement and boundary

- `ENG-PLACEMENT-01` two targets in different groups create two boundary batches.
- `ENG-PLACEMENT-02` same-group target remains local.
- `ENG-PLACEMENT-03` missing placement mapping fails deterministically.

### Child parity

- `ENG-CHILD-01` child node requiring DI dependency resolves dependency successfully.
- `ENG-CHILD-02` child node sees context metadata parity with parent.
- `ENG-CHILD-03` child invocation emits observability callbacks consistently.

---

## Exit criteria

- Runner no longer owns reply correlation policy.
- Router contract is explicit about local/boundary/terminal outcomes.
- Boundary dispatch uses per-target placement map.
- Child execution path follows parent invocation model.
- Existing deterministic outputs remain unchanged in memory profile.

---

## Next iteration handoff

This plan is complete for its original scope.

Next cleanup wave (strict runtime schema, routing service rename, runner purity hardening,
legacy-tail removal) is tracked in:

- [engine_runtime_contract_cleanup_tdd_plan](engine_runtime_contract_cleanup_tdd_plan.md)
