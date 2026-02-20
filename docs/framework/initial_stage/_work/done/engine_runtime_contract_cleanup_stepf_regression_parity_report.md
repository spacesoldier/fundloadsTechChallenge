# Engine runtime cleanup Step F: regression and parity report

## Scope

This report closes Step F from:

- [engine_runtime_contract_cleanup_tdd_plan](engine_runtime_contract_cleanup_tdd_plan.md)

Step F objectives:

1. Run focused runner/routing/lifecycle/reply contract suites.
2. Run process-supervisor and remote-handoff compatibility suites.
3. Re-run deterministic integration suites.
4. Verify memory-profile CLI parity (baseline + experiment) with `jq -cS` + `diff -u`.

Date: 2026-02-13

---

## Test matrix

Focused runner/routing/lifecycle/reply suites:

- `tests/stream_kernel/execution/test_runner_interface.py`
- `tests/stream_kernel/execution/test_runner_context_integration.py`
- `tests/stream_kernel/execution/test_runner_routing_integration.py`
- `tests/stream_kernel/execution/test_runner_reply_correlation.py`
- `tests/stream_kernel/execution/test_engine_target_model_contract.py`
- `tests/stream_kernel/integration/test_routing_port.py`
- `tests/stream_kernel/app/test_framework_consumer_registry.py`

Process-supervisor/handoff + compatibility suites:

- `tests/stream_kernel/execution/test_remote_handoff_contract.py`
- `tests/stream_kernel/execution/test_child_bootstrap.py`
- `tests/stream_kernel/execution/test_builder.py`
- `tests/stream_kernel/execution/test_reply_waiter_contract.py`
- `tests/stream_kernel/execution/test_secure_tcp_transport.py`
- `tests/stream_kernel/execution/test_bootstrap_keys.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/integration/test_work_queue.py`

Deterministic integration suites:

- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`
- `tests/usecases/steps/test_compute_features.py`

---

## Executed commands and results

### 1) Focused runner/routing/lifecycle/reply suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_runner_interface.py \
  tests/stream_kernel/execution/test_runner_context_integration.py \
  tests/stream_kernel/execution/test_runner_routing_integration.py \
  tests/stream_kernel/execution/test_runner_reply_correlation.py \
  tests/stream_kernel/execution/test_engine_target_model_contract.py \
  tests/stream_kernel/integration/test_routing_port.py \
  tests/stream_kernel/app/test_framework_consumer_registry.py
```

Result:

- `pass`.

### 2) Process-supervisor/handoff + compatibility suites

```bash
.venv/bin/pytest -q \
  tests/stream_kernel/execution/test_remote_handoff_contract.py \
  tests/stream_kernel/execution/test_child_bootstrap.py \
  tests/stream_kernel/execution/test_builder.py \
  tests/stream_kernel/execution/test_reply_waiter_contract.py \
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

---

## CLI parity verification against reference outputs (memory profile)

### Baseline scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/engine_runtime_cleanup_stepf_baseline_output.txt

jq -cS . /tmp/engine_runtime_cleanup_stepf_baseline_output.txt > /tmp/engine_runtime_cleanup_stepf_baseline_out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/engine_runtime_cleanup_stepf_baseline_ref.norm
diff -u /tmp/engine_runtime_cleanup_stepf_baseline_ref.norm /tmp/engine_runtime_cleanup_stepf_baseline_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

### Experiment scenario

```bash
PYTHONPATH=src .venv/bin/python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output /tmp/engine_runtime_cleanup_stepf_experiment_output.txt

jq -cS . /tmp/engine_runtime_cleanup_stepf_experiment_output.txt > /tmp/engine_runtime_cleanup_stepf_experiment_out.norm
jq -cS . docs/analysis/data/assets/output_exp_mp.txt > /tmp/engine_runtime_cleanup_stepf_experiment_ref.norm
diff -u /tmp/engine_runtime_cleanup_stepf_experiment_ref.norm /tmp/engine_runtime_cleanup_stepf_experiment_out.norm
```

Result:

- `diff` is empty (exact normalized parity).

---

## Exit criteria checklist (Step F)

- [x] Focused runner/routing/lifecycle/reply suites executed and green.
- [x] Process-supervisor/handoff compatibility suites executed and green.
- [x] Deterministic integration suites executed and green.
- [x] Memory-profile parity against reference outputs confirmed (baseline + experiment).

Step F status: complete.
