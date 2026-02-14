# Web Phase 5pre Step H: regression and parity report

## Scope

This report closes Phase 5pre Step H from:

- [web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan](web_phase5pre_multiprocess_supervisor_and_observability_tdd_plan.md)

Step H objectives:

1. Run existing Phase 2/3/4bis compatibility suites.
2. Run new Phase 5pre supervisor/control/observability suites.
3. Verify baseline + experiment CLI parity against reference outputs.
4. Re-check Step G smoke topology in consolidated execution run.

Date: 2026-02-13

---

## Test matrix

Phase 2/3/4bis compatibility suites:

- `tests/stream_kernel/execution/orchestration/test_builder.py`
- `tests/stream_kernel/execution/orchestration/test_remote_handoff_contract.py`
- `tests/stream_kernel/execution/orchestration/test_child_bootstrap.py`
- `tests/stream_kernel/execution/transport/test_secure_tcp_transport.py`
- `tests/stream_kernel/execution/transport/test_bootstrap_keys.py`
- `tests/stream_kernel/execution/transport/test_control_plane.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/integration/test_work_queue.py`
- `tests/stream_kernel/platform/services/test_reply_waiter_contract.py`
- `tests/stream_kernel/execution/runtime/test_runner_reply_correlation.py`

Phase 5pre suites:

- `tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`
- `tests/stream_kernel/platform/services/test_bootstrap_supervisor_event_fallback.py`
- `tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py`
- `tests/adapters/test_trace_sinks.py`
- `tests/stream_kernel/adapters/test_observability_adapters.py`
- `tests/stream_kernel/observability/test_tracing_observer_factory.py`
- `tests/stream_kernel/observability/test_tracing_observer.py`
- `tests/stream_kernel/app/test_tracing_runtime.py`
- `tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py`

Deterministic integration suites:

- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`
- `tests/usecases/steps/test_compute_features.py`

---

## Executed commands and results

### 1) Phase 2/3/4bis compatibility batch

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/orchestration/test_builder.py \
  tests/stream_kernel/execution/orchestration/test_remote_handoff_contract.py \
  tests/stream_kernel/execution/orchestration/test_child_bootstrap.py \
  tests/stream_kernel/execution/transport/test_secure_tcp_transport.py \
  tests/stream_kernel/execution/transport/test_bootstrap_keys.py \
  tests/stream_kernel/execution/transport/test_control_plane.py \
  tests/stream_kernel/app/test_framework_run.py \
  tests/stream_kernel/config/test_newgen_validator.py \
  tests/stream_kernel/integration/test_work_queue.py \
  tests/stream_kernel/platform/services/test_reply_waiter_contract.py \
  tests/stream_kernel/execution/runtime/test_runner_reply_correlation.py
```

Result:

- `pass` (with `1 skipped` in environment-dependent checks).

### 2) Phase 5pre supervisor/observability batch

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py \
  tests/stream_kernel/platform/services/test_bootstrap_supervisor_event_fallback.py \
  tests/stream_kernel/platform/services/test_bootstrap_supervisor_boundary_delegation.py \
  tests/adapters/test_trace_sinks.py \
  tests/stream_kernel/adapters/test_observability_adapters.py \
  tests/stream_kernel/observability/test_tracing_observer_factory.py \
  tests/stream_kernel/observability/test_tracing_observer.py \
  tests/stream_kernel/app/test_tracing_runtime.py \
  tests/stream_kernel/execution/orchestration/test_process_supervisor_smoke_topology.py
```

Result:

- `pass` (with `1 skipped` in environment-dependent checks).

### 3) Deterministic integration batch

```bash
.venv/bin/pytest -q \
  tests/integration/test_end_to_end_baseline_limits.py \
  tests/integration/test_end_to_end_experiment_features.py \
  tests/usecases/steps/test_compute_features.py
```

Result:

- `pass`.

---

## CLI parity verification against reference outputs (memory profile)

### Baseline scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/phase5pre_steph_baseline_output.txt

jq -cS . /tmp/phase5pre_steph_baseline_output.txt > /tmp/phase5pre_steph_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/phase5pre_steph_baseline_ref.norm
diff -u /tmp/phase5pre_steph_baseline_ref.norm /tmp/phase5pre_steph_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/phase5pre_steph_experiment_output.txt

jq -cS . /tmp/phase5pre_steph_experiment_output.txt > /tmp/phase5pre_steph_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/phase5pre_steph_experiment_ref.norm
diff -u /tmp/phase5pre_steph_experiment_ref.norm /tmp/phase5pre_steph_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Exit criteria checklist (Step H)

- [x] Existing Phase 2/3/4bis compatibility suites are green.
- [x] New Phase 5pre supervisor/control/observability suites are green.
- [x] Baseline + experiment CLI parity is confirmed (empty normalized diffs).
- [x] Step G smoke topology remains green in consolidated regression run.

Step H status: complete.
