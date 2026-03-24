# Skill: Add Routing Rule

## Use When
- Need to adjust routing for payload types/targets/lanes.

## Inputs
- Payload/token type.
- Source constraints.
- Target consumers/lanes.

## Output
- Routing table update.
- Dynamic registry update if needed.
- Regression tests.

## Procedure
1. Update routing defaults/service tables.
2. Keep lane semantics explicit (control/data/obs).
3. Add tests for no-consumer and expected route cases.
4. Verify no silent fallback behavior change.

## Mandatory Checks
```bash
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/control_plane tests/stream_kernel/execution/transport
```

