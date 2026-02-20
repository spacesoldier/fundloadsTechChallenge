# Phase E: urllib3 backend (TDD)

## Status

- [x] `urllib3` backend implemented (pooled sync transport).
- [x] `OBS-U3-01..05` tests added and green.
- [x] `settings.urllib3` config contract normalized and wired.

## Objective

Add low-level pooled sync backend using `urllib3.PoolManager`.

## Deliverables

- `urllib3` transport implementation;
- explicit pool sizing and timeout settings;
- deterministic retry strategy mapping.
- config-level `settings.urllib3` normalization.

## RED tests

- `OBS-U3-01` backend sends OTLP payload using `urllib3` request path.
- `OBS-U3-02` pool manager is reused across exports.
- `OBS-U3-03` status>=400 path increments dropped/error counters correctly.
- `OBS-U3-04` timeout handling is isolated and does not break runner flow.
- `OBS-U3-05` headers and content-type are set as required.

## GREEN target

- backend selectable via `backend=urllib3`;
- behavior parity with requests/httpx payload contract.

## Refactor

- align HTTP error categorization across all HTTP backends.

## Exit criteria

- backend tests green;
- transport-specific diagnostics visible in exporter diagnostics snapshot.

## Implementation notes

- keep common OTLP payload builder untouched;
- support optional `settings.urllib3`:
  - `num_pools`
  - `maxsize`
  - `block`
  - `timeout_seconds` (transport timeout override)
- keep exporter-failure isolation invariant.

## Validation commands

- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'obs_u3_'`
- `.venv/bin/pytest -q tests/adapters/test_trace_sinks.py -k 'otel_otlp_trace_sink'`
- `.venv/bin/pytest -q tests/stream_kernel/config/test_newgen_validator.py -k 'obs_u3_cfg_'`
