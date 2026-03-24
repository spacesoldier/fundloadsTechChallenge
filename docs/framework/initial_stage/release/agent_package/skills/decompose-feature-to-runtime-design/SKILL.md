# Skill: decompose-feature-to-runtime-design

## Goal

Turn a feature/request description into an executable runtime design for Ringo.

## Inputs

- feature text (business requirement)
- existing module context (if any)
- constraints (throughput, determinism, observability, ordering)

## Output

- message contract list
- node/service/store/adapter breakdown
- config knobs and defaults
- process-group placement proposal
- deterministic test plan

## Procedure

1. Extract invariants:
- deterministic behavior requirements
- ordering requirements
- failure/retry/idempotency requirements

2. Define message contracts:
- input payload model(s)
- intermediate payload model(s)
- terminal output model(s)

3. Build runtime decomposition:
- node responsibilities
- service operations
- mutable state ownership (stores)
- external boundaries (ports/adapters)

4. Define configurability:
- per-node settings
- runtime/process-group settings
- observability settings

5. Define test matrix:
- unit tests for service/store invariants
- node behavior tests
- e2e deterministic path test

## Guardrails

- No off-graph bypass for core data flow.
- No hidden mutable globals.
- No blocking IO in event-loop hot path.
