# Skill: Wire Control-Plane Node

## Use When
- Need to add/update a control-plane system node and routing bindings.

## Inputs
- Event types.
- Node name.
- Expected side effects.

## Output
- Node implementation.
- Plan-builder consumer/emitter wiring.
- Tests for route and state transition.

## Procedure
1. Add node with explicit `consumes`/`emits`.
2. Register in static/plan builder graph.
3. Add consumer bindings.
4. Add tests for happy path + missing-route failure.

## Mandatory Checks
```bash
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/control_plane
```

