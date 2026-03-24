# Skill: scaffold-port-and-adapter

## Goal

Introduce or extend external integration via port/adapter boundary.

## Inputs

- integration purpose (file, DB, API, queue, etc.)
- required operations
- sync/async constraints

## Output

- port interface
- adapter implementation
- DI/injection registration
- validation and runtime tests

## Procedure

1. Define port contract first (what business needs, not transport details).
2. Implement adapter:
- prefer async-native client path
- if blocking client, isolate via thread/process wrapper
3. Register binding in runtime assembly.
4. Add tests:
- settings validation
- successful call path
- failure/retry behavior if required

## Guardrails

- Nodes/services depend on port, not concrete adapter.
- Adapter must not block event loop hot path.
- Keep payloads serializable and deterministic.
