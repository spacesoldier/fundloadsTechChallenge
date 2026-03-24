# CLAUDE.md (Template) for Ringo Framework Repositories

This file mirrors AGENTS-level constraints for Claude-style agents.

## Core Runtime Contract

1. Data path stays on-graph through nodes.
2. Services encapsulate operations and use injected dependencies.
3. Mutable state is isolated in stores.
4. Control-plane and data-plane semantics must not be mixed.

## Hard Prohibitions

1. No off-graph transport shortcuts in business flow.
2. No silent fallback routing.
3. No global mutable runtime state.
4. No blocking I/O in hot async paths.

## Mandatory Delivery Contract

1. State what changed.
2. State what tests/checks were run.
3. State what remains unverified.
4. Do not claim checks that were not executed.

## Minimum Checks

```bash
.venv/bin/python -m pytest -q tests/stream_kernel
.venv/bin/python -m pytest -q tests/architecture
.venv/bin/python -m pytest -q tests/smoke
```

When control-plane/runtime internals are touched:

```bash
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/control_plane
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/lifecycle
```

Fast module invariant check:

```bash
.venv/bin/python -m stream_kernel.doctor check-module src/stream_kernel/adapters/file_io.py --json
```
