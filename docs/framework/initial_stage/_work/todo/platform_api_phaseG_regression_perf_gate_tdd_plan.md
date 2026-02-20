# Platform API Phase G: regression and performance gate (TDD)

## Status

- [x] Step A — docs + RED tests (`API-REG-01..03`)
- [x] Step B — GREEN implementation (pass-through parity + deterministic harness)
- [x] Step C — perf run + regression gate

## Goal

Close API policy rollout with parity and performance safety checks:

- prove baseline behavior is unchanged when policies are disabled;
- prove deterministic behavior under identical input/time source when policies are enabled;
- commit a repeatable throughput/latency characterization report.

---

## Contracts

### 1) Baseline parity (policies disabled)

- outbound policy service in pass-through mode must preserve call semantics:
  - same return value / raised exception;
  - no policy-side rejects/retries/circuit transitions;
  - no policy diagnostic markers when no policies are configured.

### 2) Determinism (policies enabled)

- with fixed time source and identical request sequence:
  - outcomes are deterministic;
  - counters are deterministic;
  - decision-marker sequence is deterministic.

### 3) Characterization report

- commit a markdown report with:
  - command used;
  - environment snapshot;
  - throughput/latency measurements;
  - policy-off vs policy-on comparison notes.

---

## TDD steps

### Step A — docs + RED tests

- `API-REG-01` pass-through parity in policy-disabled mode.
- `API-REG-02` deterministic policy-enabled outcomes for identical input/time.
- `API-REG-03` committed perf report presence/format check.

### Step B — GREEN implementation

- adjust outbound service behavior to satisfy disabled-mode parity contract;
- add deterministic test harness inputs for enabled-mode parity.

### Step C — perf run + regression

- run focused and integration regressions;
- generate/update characterization report and keep reference command.

---

## Test catalog

- `tests/stream_kernel/platform/services/api/test_outbound_api_service.py`
  - `test_api_reg_01_policy_disabled_mode_is_passthrough_and_marker_free`
  - `test_api_reg_02_policy_enabled_mode_is_deterministic_for_same_time_source`
- `tests/stream_kernel/platform/services/api/test_outbound_perf_report.py`
  - `test_api_reg_03_perf_report_is_committed_with_required_sections`

## Artifacts

- [platform_api_phaseG_regression_perf_report](platform_api_phaseG_regression_perf_report.md)

## Validation commands

- `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_outbound_api_service.py -k 'api_reg_' tests/stream_kernel/platform/services/api/test_outbound_perf_report.py`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/api/test_rate_limiter_service.py tests/stream_kernel/platform/services/api/test_outbound_api_service.py tests/stream_kernel/platform/services/api/test_outbound_perf_report.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k 'runtime_api_policy_bindings or api_ing_0'`
- `.venv/bin/pytest -q tests/integration/test_end_to_end_baseline_limits.py tests/integration/test_end_to_end_experiment_features.py`
