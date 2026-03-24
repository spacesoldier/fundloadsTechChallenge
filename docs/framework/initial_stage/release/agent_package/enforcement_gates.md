# Enforcement Gates for Agent Work

This checklist defines hard quality gates. If a gate fails, change is incomplete.

## Gate A: Architecture Invariants

1. No direct off-graph message handoff in business path.
2. Node/service/store boundaries respected.
3. No hidden fallback routes changing behavior silently.

Validation:
- architecture tests under `tests/architecture` or equivalent.

Validation commands:

```bash
.venv/bin/python -m pytest -q tests/architecture
```

## Gate B: Runtime Safety

1. No blocking operations in async hot paths.
2. Lifecycle transitions are explicit and test-covered.
3. Shutdown/drain path has deterministic tests.

Validation:
- targeted tests for touched control-plane/runtime modules.

## Gate C: Transport Discipline

1. Lane usage matches message semantics.
2. Control events on control lane.
3. Business envelopes on data plane.
4. Observability flow route is explicit and test-covered.

Validation:
- integration tests for ingress/transform/egress/observability interactions.

Validation commands:

```bash
.venv/bin/python -m pytest -q tests/smoke
```

## Gate D: Reproducibility

1. Output determinism preserved.
2. Tests pass in local venv.
3. No undocumented assumptions.

Validation commands (default):

```bash
.venv/bin/python -m pytest -q tests/stream_kernel
.venv/bin/python -m pytest -q tests/architecture
.venv/bin/python -m pytest -q tests/smoke
```

When control-plane touched:

```bash
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/control_plane
.venv/bin/python -m pytest -q tests/stream_kernel/execution/orchestration/lifecycle
```

Smoke experiment (optional but recommended for runtime changes):

```bash
./scripts/run_3x_check.sh --config src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml --runs 1 --debug-run 0 --timeout 15
```

## Gate E: Module Doctor Check

Use doctor for fast module-level invariant checks before full runtime validation.

```bash
.venv/bin/python -m stream_kernel.doctor check-module src/stream_kernel/adapters/file_io.py --json
```
