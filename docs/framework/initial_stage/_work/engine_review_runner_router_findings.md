# Engine review: runner/router/process handoff findings

## Context

Review target:

- `src/stream_kernel/execution/runner.py`
- `src/stream_kernel/routing/router.py`
- `src/stream_kernel/integration/routing_port.py`
- `src/stream_kernel/execution/lifecycle_orchestration.py`
- `src/stream_kernel/execution/child_bootstrap.py`

Goal:

- assess architecture health after process-fork and boundary handoff phases;
- identify responsibility leaks between runner/router/platform services;
- define concrete cleanup direction for next implementation steps.

---

## Findings

### High severity

1. Process-group dispatch is still global, not node-placement-driven.
   - `src/stream_kernel/execution/lifecycle_orchestration.py:379`
   - `src/stream_kernel/execution/lifecycle_orchestration.py:414`
   - Current behavior chooses a single `dispatch_group` for the whole boundary batch (`_select_dispatch_group`), instead of resolving target group per routed node from execution placement.
   - Impact: real multi-group execution partitioning is not active yet.

2. Child execution bypasses framework DI/context invocation model.
   - `src/stream_kernel/execution/child_bootstrap.py:199`
   - `src/stream_kernel/execution/child_bootstrap.py:233`
   - `src/stream_kernel/execution/child_bootstrap.py:235`
   - Child nodes are invoked as `step(payload, {})` with ad-hoc callable construction; this bypasses scenario-scoped injection and metadata context conventions used by main runner flow.
   - Impact: runtime behavior can diverge between parent and child processes.

3. Reply-correlation logic is embedded in runner hot path.
   - `src/stream_kernel/execution/runner.py:116`
   - `src/stream_kernel/execution/runner.py:121`
   - `src/stream_kernel/execution/runner.py:168`
   - `src/stream_kernel/execution/runner.py:169`
   - Runner performs waiter registration/completion directly and owns reply timeout policy.
   - Impact: execution engine mixes orchestration and transport/reply concerns.

### Medium severity

4. Envelope with `reply_to` but without explicit `target` can skip waiter registration.
   - `src/stream_kernel/execution/runner.py:158`
   - `src/stream_kernel/execution/runner.py:193`
   - Waiter registration currently exists only in `Envelope && target is not None` branch.
   - Impact: terminal events may be classified as late/dropped despite valid request/reply intent.

5. Runner has hardcoded execution and sink conventions.
   - `src/stream_kernel/execution/runner.py:29`
   - `src/stream_kernel/execution/runner.py:67`
   - `src/stream_kernel/execution/builder.py:55`
   - `src/stream_kernel/execution/builder.py:635`
   - Hardcoded `execution.cpu` qualifier and `sink:` name prefix are policy details inside engine runtime.
   - Impact: weak extensibility for async/celery/gpu profiles and non-prefix-based sink strategies.

6. Router known-node model is derived from consumer map only.
   - `src/stream_kernel/routing/router.py:14`
   - `src/stream_kernel/routing/router.py:21`
   - `src/stream_kernel/integration/routing_port.py:43`
   - Router validates target existence by `_known_nodes` built from consumer lists, not from a dedicated graph/node registry contract.
   - Impact: "existing node" and "consumes token" are coupled; diagnostics can be misleading.

### Low severity

7. RoutingPort rebuilds Router object on each route call.
   - `src/stream_kernel/integration/routing_port.py:22`
   - `src/stream_kernel/integration/routing_port.py:25`
   - Not functionally wrong, but extra per-call allocations on hot path.

---

## Review summary

- Platform DI wiring exists and works for baseline runtime flow.
- Core architectural gap is not DI itself but boundary execution semantics:
  - dispatch placement is still coarse;
  - child invocation model is not equivalent to parent runner model.
- Runner remains overloaded with reply responsibilities and should be narrowed to pure execution.

---

## Proposal: what to do next

### 1) Split execution and reply responsibilities

Introduce a dedicated platform service, e.g. `ReplyCoordinatorService`, and move from runner:

- waiter registration policy;
- terminal completion policy;
- timeout defaults/correlation policy.

Runner responsibility should become:

- execute node;
- emit outputs;
- call router and work queue only.

### 2) Make router/reply handling optional and cheap

Recommended model:

1. Ingress registers correlation once (`trace_id -> reply endpoint`) only if request/reply is requested.
2. Router/runner do not inspect reply sections at every hop.
3. Only terminal outputs pass through `ReplyCoordinatorService.complete_if_waiting(trace_id, terminal)`.
4. If no waiter exists, terminal is ignored (or logged) in O(1) dictionary lookup.

This is the common approach in message systems: correlation manager is active only for tracked traces, not for every message edge.

### 3) Per-node placement for remote handoff

Replace single `_select_dispatch_group` logic with placement map from execution planning:

- `target node -> process_group`;
- boundary dispatch built per target group batch;
- local path for same-group nodes only.

### 4) Unify child and parent execution pipeline

Child runtime should execute node calls through the same orchestration path as parent:

- context retrieval/seeding rules;
- DI-applied node/service instances;
- observability hooks.

Avoid direct `step(payload, {})` calls in child loop.

### 5) Remove hardcoded runtime conventions

Move these into runtime config/contracts:

- queue qualifier (`execution.cpu`);
- sink detection strategy (not by string prefix);
- reply timeout default.

---

## Request/reply design options (without per-step slowdown)

### Option A (recommended): terminal-only correlation lookup

- Keep a waiter map keyed by `trace_id`.
- On terminal event only, perform single O(1) lookup.
- No reply-specific checks on non-terminal outputs.

Pros:

- minimal overhead;
- naturally supports fire-and-forget flows.

### Option B: explicit reply channel type

- Terminal events for request/reply go to dedicated `response` port/topic.
- Coordinator consumes only that stream.

Pros:

- clearer contracts at boundaries.

Cons:

- more wiring complexity.

### Option C: router fallback for unroutable terminal envelopes

- If router finds no downstream consumer and payload is terminal, it asks reply coordinator.

Pros:

- no extra node code.

Cons:

- implicit behavior; harder to debug than Option A.

---

## Suggested TDD cases for cleanup

- `ENG-REPLY-01` runner has no direct waiter register/complete calls.
- `ENG-REPLY-02` terminal completion is handled by reply coordinator service only.
- `ENG-REPLY-03` envelope with `reply_to` and no initial target still registers waiter if correlation is requested.
- `ENG-PLACEMENT-01` per-target process group dispatch map is honored.
- `ENG-CHILD-01` child execution uses same DI/context contract as parent runner.

Implementation plan:

- [engine runner/router target model plan](engine_runner_router_target_model_tdd_plan.md)
