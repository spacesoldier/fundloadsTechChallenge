# Web Phase 3 Step F: regression and parity report

## Scope

This report closes Phase 3 Step F from:

- [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)

Step F objectives:

1. Run focused process-bootstrap suites.
2. Run existing Phase 2 IPC/lifecycle suites for backward compatibility.
3. Verify memory-profile parity remains unchanged.
4. Verify tcp-local profile still rejects invalid/replay/oversized frames.

Date: 2026-02-12

---

## Test matrix

Focused Phase 3 suites:

- `tests/stream_kernel/execution/test_builder.py`
- `tests/stream_kernel/execution/test_bootstrap_keys.py`
- `tests/stream_kernel/execution/test_child_bootstrap.py`
- `tests/stream_kernel/execution/test_secure_tcp_transport.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/integration/test_work_queue.py`

Phase 2 backward-compat suites:

- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`
- `tests/usecases/steps/test_compute_features.py`

Targeted tcp-local reject checks:

- replay reject
- oversized reject
- invalid signature reject

---

## Executed commands and results

### 1) Focused Phase 3 + runtime suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_builder.py \
  tests/stream_kernel/execution/test_secure_tcp_transport.py \
  tests/stream_kernel/execution/test_bootstrap_keys.py \
  tests/stream_kernel/execution/test_child_bootstrap.py \
  tests/stream_kernel/app/test_framework_run.py \
  tests/stream_kernel/config/test_newgen_validator.py \
  tests/stream_kernel/integration/test_work_queue.py
```

Result:

- `pass` (with `1 skipped` in socket-dependent transport test as expected for this environment).

### 2) Phase 2 backward-compat regression suites

```bash
.venv/bin/pytest -q \
  tests/integration/test_end_to_end_baseline_limits.py \
  tests/integration/test_end_to_end_experiment_features.py \
  tests/usecases/steps/test_compute_features.py
```

Result:

- `pass`.

### 3) Targeted tcp-local reject checks

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
  --output /tmp/phase3_stepf_baseline_output.txt

jq -cS . /tmp/phase3_stepf_baseline_output.txt > /tmp/phase3_stepf_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/phase3_stepf_baseline_ref.norm
diff -u /tmp/phase3_stepf_baseline_ref.norm /tmp/phase3_stepf_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/phase3_stepf_experiment_output.txt

jq -cS . /tmp/phase3_stepf_experiment_output.txt > /tmp/phase3_stepf_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/phase3_stepf_experiment_ref.norm
diff -u /tmp/phase3_stepf_experiment_ref.norm /tmp/phase3_stepf_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Exit criteria checklist (Step F)

- [x] Focused process-bootstrap suites executed and green.
- [x] Existing Phase 2 IPC/lifecycle regression suites executed and green.
- [x] Memory-profile parity against reference outputs confirmed (baseline + experiment).
- [x] Tcp-local reject guard behavior (invalid/replay/oversized) remains green.

Step F status: complete.
