# Skill: map-config-and-process-groups

## Goal

Map node topology into runtime config and process-group layout.

## Inputs

- node set and dependencies
- throughput/latency targets
- observability and shutdown requirements

## Output

- process group assignment proposal
- config patch (runtime/process_groups/node config)
- rationale for routing and lifecycle impact

## Procedure

1. Group nodes by data-flow stage.
2. Separate business data-plane from control/observability-plane concerns.
3. Assign worker counts per group and justify.
4. Define config defaults:
- batching/window knobs
- queue/transport constraints
- observability limits
5. Add/adjust tests for startup and completion behavior.

## Guardrails

- Do not reintroduce hidden star-routing dependencies if ring/data-plane is required.
- Keep completion semantics explicit and testable.
- Keep control-plane signals isolated from business payload contracts.
