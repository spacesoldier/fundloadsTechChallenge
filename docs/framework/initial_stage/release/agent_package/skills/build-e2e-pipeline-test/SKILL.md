# Skill: build-e2e-pipeline-test

## Goal

Create deterministic e2e tests for pipeline behavior (single or multi-process).

## Inputs

- pipeline shape (ingress/transform/egress/observability)
- input fixture and expected output
- completion criteria (including shutdown/drain expectations)

## Output

- e2e test(s)
- test fixtures
- assertions for data correctness and lifecycle completion

## Procedure

1. Build minimal fixture with known expected outputs.
2. Add deterministic assertions:
- exact output count/content
- stable ordering where required
3. Add lifecycle assertions:
- startup readiness reached
- completion signal(s) observed
- process exits without timeout in normal path
4. Add observability sanity checks:
- key traces/logs/metrics emitted as expected
5. Add regression case for previously failing bottleneck.

## Guardrails

- Avoid external dependencies in baseline e2e unless explicitly needed.
- Keep fixtures small by default; add stress profile as separate test/config.
- Never accept silent data loss as success.
