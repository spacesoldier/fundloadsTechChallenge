# Child bootstrap dismantling + root/leaf module split (TDD plan)

## Purpose

Remove legacy `execution.orchestration.child_bootstrap` hardcoded child-process
bootstrap flow and replace it with platform rails:

- `@node`-driven leaf init/control-plane flow
- `@service`-driven leaf runtime activation / command handling
- adapters only for transport/IO (no orchestration logic)

In parallel, split orchestration packages into `root/` and `leaf/` submodules
to prevent new god-modules in `control_plane` and `lifecycle`.

Related docs:

- `docs/framework/initial_stage/web/analysis/Control-plane bootstrap root-leaf sequences.md`
- `docs/framework/initial_stage/web/analysis/IPC transport and worker lifecycle services.md`

## Scope

- Extract child activation/bootstrap logic out of `child_bootstrap.py`
- Introduce leaf runtime activation service contract
- Move leaf init flow to nodes/services activated by `ControlPlaneLeafPulse`
- Organize `control_plane` and `lifecycle` modules into `root/` and `leaf/`

Non-goals (this plan)

- TCP transport migration (current work remains on IPC/pipe path)
- Streaming boundary handoff refactor (tracked separately)
- Redis/external state backends

## Current state (baseline)

Implemented already:

- root/leaf typed control-plane events (`hello/config/ack`, boundary, stop)
- root reply ingress / startup handshake / boundary / stop orchestration services
- leaf command loop service and leaf worker control-plane entrypoint
- real IPC (pipe) e2e tests for handshake/boundary/stop/full-flow

Progress already completed in this line:

- top-level `src/stream_kernel/execution/orchestration/child_bootstrap.py`
  removed; legacy child bootstrap code was moved and then split under `lifecycle/leaf/*`
- leaf package split started (`leaf/command_loop_service.py`,
  `leaf/control_plane_service.py`, `leaf/runtime_activation_service.py`,
  `leaf/runtime_bootstrap_service.py`, `leaf/runtime_boundary_service.py`)
- boundary execution loop extracted into
  `leaf/boundary_runtime.py`
- child runtime bootstrap assembly folded into `leaf/runtime_bootstrap_service.py`
- bootstrap assembly support helpers extracted into `leaf/bootstrap_support.py`
- child bootstrap bundle factory extracted into `leaf/bootstrap_bundle.py`

Remaining legacy hotspot:

- `src/stream_kernel/execution/orchestration/lifecycle/leaf/runtime_bootstrap_service.py`
  still contains hardcoded child runtime bootstrap / DI assembly glue that should
  be decomposed into leaf services + node flows.

## Phase A — contract + structure freeze (RED)

### A1. Leaf runtime activation service contract

Add RED tests for a new service contract (name may be finalized during coding):

- `LeafRuntimeActivationService.apply_config(session, card) -> ControlPlaneLeafConfigAckEvent`
- Service owns subset discovery + activation preparation
- Service appends discovery items via injected discovery/state services
- Service returns deterministic `rejected` ack on failure

Tests must not depend on docs file paths.

### A2. Command loop delegation contract

Add RED tests asserting `LeafWorkerCommandLoopService` delegates config-card
handling to activation service instead of embedding activation logic directly.

### A3. Packaging target contract

Add RED tests for import/export layout (code contracts only, no doc-path checks):

- role-local modules can be imported:
  - `...control_plane.root.*`
  - `...lifecycle.leaf.*`
- public package exports remain stable (or explicitly versioned)

## Phase B — leaf activation extraction (GREEN)

- Introduce `@service` leaf runtime activation service
- Move config-card subset discovery / activation logic out of
  `leaf_worker_command_loop_service.py`
- Keep typed ack behavior identical
- Update existing leaf command loop tests to assert delegation

## Phase C — leaf package split (GREEN)

- Create `src/stream_kernel/execution/orchestration/lifecycle/leaf/`
- Move leaf modules incrementally:
  - runtime session/helpers
  - command loop service (`lifecycle/leaf/command_loop_service.py`)
  - control-plane worker entry service (`lifecycle/leaf/control_plane_service.py`)
  - activation service (new)
- Keep package exports in `lifecycle/__init__.py` stable while migration is in progress

## Phase D — control_plane package split (GREEN)

- Create `src/stream_kernel/execution/orchestration/control_plane/root/`
- Move root-only orchestration services:
  - reply ingress
  - startup handshake
  - leaf command service
  - boundary/stop/shutdown services
  - root runtime bootstrap service
- Keep `control_plane/system_nodes.py` as temporary aggregate if needed

## Phase E — child_bootstrap dismantling (GREEN)

- Replace hardcoded child bootstrap flow with leaf init nodes/services:
  - process entrypoint enqueues `ControlPlaneLeafPulse`
  - runner executes leaf init nodes
  - activation happens via leaf services
- Remove direct callers of leaf bootstrap glue paths
- Delete remaining procedural bootstrap assembly module(s) after service extraction

## Phase F — regression + e2e (GREEN)

- Existing real IPC e2e tests remain green:
  - handshake
  - boundary roundtrip
  - stop roundtrip
  - stop timeout fallback
  - full-flow
- Add/upgrade e2e covering leaf init path without `child_bootstrap`

## Done criteria

- legacy `leaf/bootstrap_runtime.py` removed
- remaining child bootstrap assembly is embedded in `leaf/runtime_bootstrap_service.py` and targeted for further service extraction
- Leaf init/activation is message-driven (`ControlPlaneLeafPulse` + typed config flow)
- `control_plane` and `lifecycle` packages have role-local `root/` and `leaf/` modules
- Public exports are stable and tests cover the new contracts
