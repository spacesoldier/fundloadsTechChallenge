# Skill: Add Resource Adapter

## Use When
- Need a new adapter with settings validation and DI registration.

## Inputs
- Adapter alias.
- Settings model.
- Contract/protocol.

## Output
- Adapter implementation.
- Registration wiring.
- Validation and runtime tests.

## Procedure
1. Define adapter settings schema.
2. Implement adapter against contract.
3. Register in adapter registry.
4. Add positive and invalid-settings tests.

## Mandatory Checks
```bash
.venv/bin/python -m pytest -q tests/stream_kernel
```

