# Skill: feature-to-pr

## Goal

Deliver one feature from requirement text to PR-ready result in a single controlled workflow.

## Inputs

- feature/request description
- target module paths
- constraints:
  - determinism
  - ordering
  - observability
  - throughput/latency (if specified)
- required checks/gates for repository

## Output

- runtime design note (messages/nodes/services/stores/adapters/config)
- implementation patch (code + docs)
- tests (unit + e2e where needed)
- gate results (what passed / what not run)
- concise PR summary with residual risks

## Workflow

1. Decompose requirement:
- use `decompose-feature-to-runtime-design` logic
- write explicit invariants and acceptance criteria

2. Implement core module slice:
- use `scaffold-node-service-store` for main processing path

3. Implement integration boundaries if needed:
- use `scaffold-port-and-adapter` for external IO/API/DB/file integration

4. Configure runtime placement when needed:
- use `map-config-and-process-groups` for process split and config knobs

5. Add deterministic e2e coverage:
- use `build-e2e-pipeline-test` for final pipeline behavior checks

6. Run mandatory checks:
- targeted tests for touched modules
- architecture/smoke gates required by repository policy

7. Produce PR-ready report:
- what changed
- what was tested
- what remains unverified
- known risks/follow-ups

## Guardrails

- No off-graph bypass for core runtime path.
- No hidden fallback behavior that changes semantics silently.
- No blocking IO in event-loop hot path.
- No claims about checks that were not executed.
