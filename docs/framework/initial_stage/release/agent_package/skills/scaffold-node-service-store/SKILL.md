# Skill: scaffold-node-service-store

## Goal

Create a coherent module slice: `@node` + `@service` + store + tests.

## Inputs

- component name
- consumed/emitted message types
- target package path

## Output

- node implementation
- service implementation
- store implementation (in-memory or injected contract)
- tests covering deterministic behavior and edge cases

## Procedure

1. Define message classes and expected transitions.
2. Implement node:
- input -> service call -> output(s)
- no external IO directly in node body
3. Implement service:
- business decisions
- calls store/port boundaries
4. Implement store contract/impl:
- explicit API for state access
- deterministic semantics
5. Add tests:
- service unit tests
- node behavior tests
- error-path tests

## Done Criteria

- Module passes targeted pytest.
- Behavior is deterministic for same input/state.
- No hidden side effects outside service/store interfaces.
