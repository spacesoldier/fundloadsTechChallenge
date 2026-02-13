# Web Phase 2 Step F: regression and parity report

## Scope

This report closes Phase 2 Step F from:

- [web_phase2_secure_tcp_runtime_integration_tdd_plan](web_phase2_secure_tcp_runtime_integration_tdd_plan.md)

Step F objectives:

1. Run focused web/transport/lifecycle suites.
2. Run baseline deterministic integration scenarios.
3. Validate memory-profile output parity against reference assets.

Date: 2026-02-12

---

## Test matrix

Focused suites (framework runtime/transport/lifecycle):

- `tests/stream_kernel/execution/test_builder.py`
- `tests/stream_kernel/execution/test_secure_tcp_transport.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/integration/test_work_queue.py`

Deterministic integration suites:

- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`

Domain/usecase regression affected during Step F:

- `tests/usecases/steps/test_compute_features.py`

---

## Executed commands and results

### 1) Focused suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_builder.py \
  tests/stream_kernel/execution/test_secure_tcp_transport.py \
  tests/stream_kernel/app/test_framework_run.py \
  tests/stream_kernel/config/test_newgen_validator.py \
  tests/stream_kernel/integration/test_work_queue.py
```

Result:

- `pass` (with `1 skipped` in socket-dependent transport test as expected for this environment).

### 2) Deterministic integration suites

```bash
.venv/bin/pytest -q \
  tests/integration/test_end_to_end_baseline_limits.py \
  tests/integration/test_end_to_end_experiment_features.py
```

Result:

- `pass`.

### 3) Full Step F consolidated run

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_builder.py \
  tests/stream_kernel/execution/test_secure_tcp_transport.py \
  tests/stream_kernel/app/test_framework_run.py \
  tests/stream_kernel/config/test_newgen_validator.py \
  tests/stream_kernel/integration/test_work_queue.py \
  tests/integration/test_end_to_end_baseline_limits.py \
  tests/integration/test_end_to_end_experiment_features.py \
  tests/usecases/steps/test_compute_features.py
```

Result:

- `pass` (with `1 skipped`).

---

## CLI parity verification against reference outputs

### Baseline scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/stepf_baseline_output.txt

jq -cS . /tmp/stepf_baseline_output.txt > /tmp/stepf_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/stepf_baseline_ref.norm
diff -u /tmp/stepf_baseline_ref.norm /tmp/stepf_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/stepf_experiment_output.txt

jq -cS . /tmp/stepf_experiment_output.txt > /tmp/stepf_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/stepf_experiment_ref.norm
diff -u /tmp/stepf_experiment_ref.norm /tmp/stepf_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Step F regression finding and fix

During parity execution, experiment CLI run initially failed with:

- `TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'`

Root cause:

- `nodes.compute_features.monday_multiplier` comes from YAML as float in CLI path;
- `ComputeFeatures` used that value directly in Decimal money arithmetic.

Fix implemented:

- normalize multiplier to `Decimal` in `ComputeFeatures` before arithmetic.
- add regression test for float-config multiplier.

Changed files:

- `src/fund_load/usecases/steps/compute_features.py`
- `tests/usecases/steps/test_compute_features.py`

---

## Exit criteria checklist (Step F)

- [x] Focused web/transport/lifecycle suites executed and green.
- [x] Baseline deterministic integration suites executed and green.
- [x] Memory-profile parity against reference outputs confirmed (baseline + experiment).
- [x] Regression discovered during parity run was fixed with test coverage.

Step F status: complete.
