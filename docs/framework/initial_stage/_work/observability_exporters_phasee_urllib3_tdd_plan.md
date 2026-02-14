# Phase E: urllib3 backend (TDD)

## Objective

Add low-level pooled sync backend using `urllib3.PoolManager`.

## Deliverables

- `urllib3` transport implementation;
- explicit pool sizing and timeout settings;
- deterministic retry strategy mapping.

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
