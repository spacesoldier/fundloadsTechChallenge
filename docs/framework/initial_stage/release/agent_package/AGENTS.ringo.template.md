# AGENTS.md (Template) for Ringo Framework Repositories

This file defines mandatory rules for coding agents in this repository.

## Primary Architecture Contract

1. Runtime data flows only through graph nodes.
2. Node receives messages and returns messages.
3. Service contains business operations and uses injected stores/adapters.
4. Store is the only mutable state holder.
5. Transport details must stay in transport layer. No ad-hoc direct calls across layers.

## Non-Negotiable Invariants

1. Do not bypass routing with off-graph shortcuts unless explicitly allowed by this repo.
2. Do not merge control-plane and data-plane payload classes.
3. Keep message envelopes deterministic and serializable.
4. Keep lifecycle transitions explicit and event-driven.
5. Keep source/sink registration and routing table updates consistent.

## Prohibited Patterns

1. Global mutable singleton state for runtime logic.
2. Thread locks around broad critical sections in hot message paths.
3. Hidden fallback routing that changes semantics silently.
4. Blocking I/O on event-loop critical path.
5. Dynamic behavior without corresponding tests.

## Minimal Correct Module Pattern

1. Define message classes.
2. Implement node(s) with clear `consumes/emits`.
3. Implement service(s) for stateful operations.
4. Implement store interface + in-memory implementation.
5. Add plan bindings and tests.

## Mandatory Workflow

1. Read relevant docs before code changes.
2. Write/adjust tests first.
3. Implement code.
4. Run mandatory checks.
5. Summarize changes and residual risks.

## Mandatory Checks (default)

Use local venv python:

```bash
.venv/bin/python -m pytest -q tests/stream_kernel
.venv/bin/python -m pytest -q tests/architecture
.venv/bin/python -m pytest -q tests/smoke
```

Targeted control-plane checks when touched:

```bash
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/control_plane
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/lifecycle
```

Fast module invariant check:

```bash
.venv/bin/python -m stream_kernel.doctor check-module src/stream_kernel/adapters/file_io.py --json
```

If experiment config is changed, run one short smoke:

```bash
./scripts/run_3x_check.sh --config src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml --runs 1 --debug-run 0 --timeout 15
```

## Response Contract For Agents

1. State exactly what was changed.
2. State exactly what was tested.
3. State what remains unverified.
4. Never claim successful checks that were not run.
