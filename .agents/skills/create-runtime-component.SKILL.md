# Skill: Create Runtime Component

## Use When
- Need a new runtime component in framework style: `node -> service -> store`.

## Inputs
- Component name.
- Message contracts (`consumes` / `emits`).
- Target module path.

## Output
- Node implementation.
- Service implementation.
- Store interface + in-memory store.
- Deterministic tests.

## Procedure
1. Read relevant architecture docs.
2. Define message contracts.
3. Add store, then service, then node.
4. Add plan bindings.
5. Write tests first, then implementation.

## Mandatory Checks
```bash
.venv/bin/python -m pytest -q tests/stream_kernel
```

