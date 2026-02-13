# Web Phase 4 Step F: regression and parity report

## Scope

This report closes Phase 4 Step F from:

- [web_phase4_reply_correlation_tdd_plan](web_phase4_reply_correlation_tdd_plan.md)

Step F objectives:

1. Run focused Phase 4 waiter/correlation suites.
2. Run existing Phase 2/3 transport+lifecycle suites for compatibility.
3. Verify memory-profile CLI parity against reference outputs.
4. Verify tcp-local reject guard behavior remains stable.

Date: 2026-02-12

---

## Test matrix

Phase 4 focused suites:

- `tests/stream_kernel/execution/test_reply_waiter_contract.py`
- `tests/stream_kernel/execution/test_runner_reply_correlation.py`
- `tests/stream_kernel/execution/test_builder.py` (reply/process_supervisor slice)

Phase 2/3 compatibility suites:

- `tests/stream_kernel/execution/test_builder.py`
- `tests/stream_kernel/execution/test_secure_tcp_transport.py`
- `tests/stream_kernel/execution/test_bootstrap_keys.py`
- `tests/stream_kernel/execution/test_child_bootstrap.py`
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

### 1) Phase 4 focused suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_reply_waiter_contract.py \
  tests/stream_kernel/execution/test_runner_reply_correlation.py \
  tests/stream_kernel/execution/test_builder.py \
  -k "reply or REPLY or PH4-D or process_supervisor"
```

Result:

- `pass`.

### 2) Phase 2/3 compatibility suites

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

- `pass` (with `1 skipped` in socket-dependent transport checks as expected for this environment).

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
  --output /tmp/phase4_stepf_baseline_output.txt

jq -cS . /tmp/phase4_stepf_baseline_output.txt > /tmp/phase4_stepf_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/phase4_stepf_baseline_ref.norm
diff -u /tmp/phase4_stepf_baseline_ref.norm /tmp/phase4_stepf_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/phase4_stepf_experiment_output.txt

jq -cS . /tmp/phase4_stepf_experiment_output.txt > /tmp/phase4_stepf_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/phase4_stepf_experiment_ref.norm
diff -u /tmp/phase4_stepf_experiment_ref.norm /tmp/phase4_stepf_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Exit criteria checklist (Step F)

- [x] Phase 4 waiter/correlation suites executed and green.
- [x] Phase 2/3 transport+lifecycle compatibility suites executed and green.
- [x] Memory-profile parity against reference outputs confirmed (baseline + experiment).
- [x] Tcp-local reject guard behavior remains green.

Step F status: complete.
