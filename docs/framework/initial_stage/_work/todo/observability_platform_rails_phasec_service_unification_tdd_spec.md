# Observability platform rails — Phase C service unification (TDD spec)

## Scope

Phase C unifies all non-node observability callbacks under a single platform service API.

Target callbacks:

- ingress registration (`trace_id`, `reply_to`)
- terminal completion event (`TerminalEvent`)
- ingress rate-limit decision
- outbound policy-chain decision
- runtime lifecycle events (start/ready/run/stop)

## Problem statement

Current runtime path still uses ad-hoc callback probing (`getattr(..., "on_*", None)`) in multiple places:

- `source_ingress.py`
- `runner.py`
- `platform/services/api/outbound.py`
- lifecycle orchestration has no explicit unified observability callback contract.

This keeps observability behavior fragmented and partially implicit.

## Phase C contract

Introduce/standardize a unified service-level contract:

- `ObservabilityPipelineService` (platform service contract)
  - extends execution-observer callbacks (`before_node`, `after_node`, `on_node_error`, `on_run_end`)
  - plus:
    - `on_ingress(...)`
    - `on_terminal_event(...)`
    - `on_ingress_rate_limit_decision(...)`
    - `on_outbound_policy_decision(...)`
    - `on_runtime_lifecycle_event(...)`

Compatibility rule:

- legacy/partial observability implementations must still work through a platform compatibility adapter;
- missing optional callbacks degrade to deterministic no-op (no runtime crash).

## TDD cases

### RED tests

`OBS-K-C-01` pipeline callbacks are available on no-op service and are side-effect free.

`OBS-K-C-02` fanout service forwards new pipeline callbacks to observers that implement them.

`OBS-K-C-03` reply-aware wrapper forwards pipeline callbacks to inner service and preserves reply coordinator behavior for ingress/terminal.

`OBS-K-C-04` source ingress path emits ingress + rate-limit decision via unified service API (no direct ad-hoc probing in call site).

`OBS-K-C-05` outbound API policy path emits policy decisions via unified service API.

`OBS-K-C-06` lifecycle-managed execution emits runtime lifecycle events through unified service.

### GREEN implementation

1. Add/complete `ObservabilityPipelineService` contract and compatibility adapter in platform observability module.
2. Wire `NoOpObservabilityService`, `FanoutObservabilityService`, `ReplyAwareObservabilityService` to this contract.
3. Update runtime call sites to use unified service API:
   - source ingress
   - sync/async runners
   - outbound API service
4. Emit lifecycle events in lifecycle orchestration via unified service.
5. Keep failure isolation: observability callback failures must not break main execution path.

## Acceptance

- no direct callback probing remains in runtime call sites listed above;
- all callbacks are routed through unified platform observability service API;
- focused RED/GREEN tests pass.

