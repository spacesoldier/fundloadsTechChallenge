# Web Phase 4bis Step F: regression and parity report

## Scope

This report closes Phase 4bis Step F from:

- [web_phase4bis_remote_execution_handoff_tdd_plan](web_phase4bis_remote_execution_handoff_tdd_plan.md)

Step F objectives:

1. Run focused remote-handoff suites.
2. Run Phase 2/3/4 compatibility suites.
3. Verify memory-profile CLI parity against reference outputs.
4. Re-check tcp-local reject guards.

Date: 2026-02-12

---

## Test matrix

Focused handoff suites:

- `tests/stream_kernel/execution/test_remote_handoff_contract.py`
- `tests/stream_kernel/execution/test_child_bootstrap.py`
- `tests/stream_kernel/execution/test_builder.py` (process-supervisor/handoff slices)

Phase 2/3/4 compatibility suites:

- `tests/stream_kernel/execution/test_reply_waiter_contract.py`
- `tests/stream_kernel/execution/test_runner_reply_correlation.py`
- `tests/stream_kernel/execution/test_secure_tcp_transport.py`
- `tests/stream_kernel/execution/test_bootstrap_keys.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/integration/test_work_queue.py`

Deterministic integration suites:

- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`
- `tests/usecases/steps/test_compute_features.py`

Targeted tcp-local reject checks:

- replay reject
- oversized reject
- invalid signature reject

---

## Executed commands and results

### 1) Focused handoff suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_remote_handoff_contract.py \
  tests/stream_kernel/execution/test_child_bootstrap.py \
  tests/stream_kernel/execution/test_builder.py \
  -k "HANDOFF or process_supervisor or PH4-D or STOP-IPC or KEY-IPC or CHILD-BOOT"
```

Result:

- `pass`.

### 2) Phase 2/3/4 compatibility suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_reply_waiter_contract.py \
  tests/stream_kernel/execution/test_runner_reply_correlation.py \
  tests/stream_kernel/execution/test_secure_tcp_transport.py \
  tests/stream_kernel/execution/test_bootstrap_keys.py \
  tests/stream_kernel/app/test_framework_run.py \
  tests/stream_kernel/config/test_newgen_validator.py \
  tests/stream_kernel/integration/test_work_queue.py
```

Result:

- `pass` (with `1 skipped` in environment-dependent transport checks).

### 3) Deterministic integration suites

```bash
.venv/bin/pytest -q \
  tests/integration/test_end_to_end_baseline_limits.py \
  tests/integration/test_end_to_end_experiment_features.py \
  tests/usecases/steps/test_compute_features.py
```

Result:

- `pass`.

### 4) Targeted tcp-local reject checks

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_secure_tcp_transport.py \
  tests/stream_kernel/execution/test_builder.py \
  -k "replay or oversized or reject or transport_reject"
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
  --output /tmp/phase4bis_stepf_baseline_output.txt

jq -cS . /tmp/phase4bis_stepf_baseline_output.txt > /tmp/phase4bis_stepf_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/phase4bis_stepf_baseline_ref.norm
diff -u /tmp/phase4bis_stepf_baseline_ref.norm /tmp/phase4bis_stepf_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/phase4bis_stepf_experiment_output.txt

jq -cS . /tmp/phase4bis_stepf_experiment_output.txt > /tmp/phase4bis_stepf_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/phase4bis_stepf_experiment_ref.norm
diff -u /tmp/phase4bis_stepf_experiment_ref.norm /tmp/phase4bis_stepf_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Exit criteria checklist (Step F)

- [x] Focused handoff suites executed and green.
- [x] Phase 2/3/4 compatibility suites executed and green.
- [x] Memory-profile parity against reference outputs confirmed (baseline + experiment).
- [x] Tcp-local reject guard behavior remains green.

Step F status: complete.
