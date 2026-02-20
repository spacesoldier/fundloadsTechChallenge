# Web Phase 3 Step A: bootstrap and key-management contract freeze

## Purpose

Freeze the contract before implementing Phase 3 runtime behavior:

- bootstrap mode selection for process isolation;
- execution IPC secret sourcing mode;
- key-derivation profile declaration;
- hard precondition checks for process-supervisor startup.

Step A is intentionally config-contract only. It does not spawn processes,
generate runtime secrets, or distribute bootstrap bundles yet.

Related:

- [web_phase3_bootstrap_process_and_secret_distribution_tdd_plan](web_phase3_bootstrap_process_and_secret_distribution_tdd_plan.md)
- [web_multiprocessing_secure_tcp_fastapi_plan](web_multiprocessing_secure_tcp_fastapi_plan.md)
- [Execution process port security profile](../web/analysis/Execution%20process%20port%20security%20profile.md)

---

## Scope and boundaries

In scope:

- validator normalization and strict checks for Phase 3 Step A fields;
- deterministic defaults for missing bootstrap/auth fields;
- runtime contract summary exposure for diagnostics and plan gating.

Out of scope:

- secret bytes generation and distribution channel;
- bootstrap supervisor implementation and worker spawning;
- lifecycle stop/drain protocol implementation.

---

## Canonical contract table

| Path | Type | Allowed values | Default | Required |
| --- | --- | --- | --- | --- |
| `runtime.platform.bootstrap.mode` | `str` | `inline`, `process_supervisor` | `inline` | no |
| `runtime.platform.execution_ipc.auth.secret_mode` | `str` | `static`, `generated` | `static` | no (under `execution_ipc`) |
| `runtime.platform.execution_ipc.auth.kdf` | `str` | `none`, `hkdf_sha256` | conditional, see rules below | no (under `execution_ipc`) |

KDF conditional defaults:

- if `secret_mode=static` and `kdf` is omitted, set `kdf=none`;
- if `secret_mode=generated` and `kdf` is omitted, set `kdf=hkdf_sha256`.

---

## Cross-field invariants

Invariant I1:

- `runtime.platform.bootstrap.mode=process_supervisor` requires
  `runtime.platform.execution_ipc.transport=tcp_local`.

Invariant I2:

- if `execution_ipc` block is absent, bootstrap mode must not be
  `process_supervisor`.

Invariant I3:

- unknown enum values for `bootstrap.mode`, `auth.secret_mode`, and `auth.kdf`
  are fail-fast validator errors.

Invariant I4:

- runtime summary must expose normalized contract fields, including defaults.

---

## Deterministic failure contract

Step A requires fail-fast validator failures with path-specific diagnostics.

Error catalog:

- `BOOT-CFG-01`:
  - condition: unsupported `runtime.platform.bootstrap.mode`;
  - expected behavior: raise `ConfigError`.
- `BOOT-CFG-02`:
  - condition: unsupported `runtime.platform.execution_ipc.auth.secret_mode`;
  - expected behavior: raise `ConfigError`.
- `BOOT-CFG-03`:
  - condition: unsupported `runtime.platform.execution_ipc.auth.kdf`;
  - expected behavior: raise `ConfigError`.
- `BOOT-CFG-05`:
  - condition: `bootstrap.mode=process_supervisor` while
    `execution_ipc.transport!=tcp_local` or missing;
  - expected behavior: raise `ConfigError`.

The exact wording may evolve, but diagnostics must include offending path and
expected allowed values or dependency requirement.

---

## Runtime summary freeze

`runtime_contract_summary(...)` must include:

- top-level `bootstrap_mode`;
- `execution_ipc.secret_mode`;
- `execution_ipc.kdf`.

Summary behavior by profile:

- memory/default profile:
  - `bootstrap_mode = "inline"`
  - `execution_ipc.secret_mode = null`
  - `execution_ipc.kdf = null`
- tcp-local profile with explicit auth section:
  - summary reflects normalized `secret_mode`/`kdf` values.

This summary contract is used for:

- diagnostics;
- release gating for phase transitions;
- deterministic test assertions.

---

## Examples

### Valid: process supervisor with generated secret

```yaml
runtime:
  platform:
    bootstrap:
      mode: process_supervisor
    execution_ipc:
      transport: tcp_local
      bind_host: 127.0.0.1
      bind_port: 0
      auth:
        mode: hmac
        secret_mode: generated
        kdf: hkdf_sha256
        ttl_seconds: 30
        nonce_cache_size: 100000
      max_payload_bytes: 1048576
```

### Valid: process supervisor with generated secret and implicit KDF default

```yaml
runtime:
  platform:
    bootstrap:
      mode: process_supervisor
    execution_ipc:
      transport: tcp_local
      auth:
        mode: hmac
        secret_mode: generated
```

Normalized expectation:

- `auth.kdf` becomes `hkdf_sha256`.

### Valid: inline mode with static secret defaults

```yaml
runtime:
  platform:
    execution_ipc:
      transport: tcp_local
      auth:
        mode: hmac
```

Normalized expectation:

- `bootstrap.mode=inline`;
- `auth.secret_mode=static`;
- `auth.kdf=none`.

### Invalid: process supervisor without tcp_local transport

```yaml
runtime:
  platform:
    bootstrap:
      mode: process_supervisor
```

Expected failure:

- `BOOT-CFG-05`.

### Invalid: unknown KDF

```yaml
runtime:
  platform:
    execution_ipc:
      transport: tcp_local
      auth:
        mode: hmac
        secret_mode: generated
        kdf: bad_kdf
```

Expected failure:

- `BOOT-CFG-03`.

---

## TDD mapping (Step A)

Validator tests:

- `BOOT-CFG-01`: `test_validate_newgen_config_rejects_unknown_bootstrap_mode`
- `BOOT-CFG-02`: `test_validate_newgen_config_rejects_unknown_execution_ipc_secret_mode`
- `BOOT-CFG-03`: `test_validate_newgen_config_rejects_unknown_execution_ipc_kdf`
- `BOOT-CFG-04`: `test_validate_newgen_config_defaults_runtime_platform_bootstrap_mode_to_inline`
- `BOOT-CFG-05`: `test_validate_newgen_config_requires_tcp_local_transport_for_process_supervisor_mode`
- positive baseline:
  - `test_validate_newgen_config_accepts_process_supervisor_mode_with_valid_execution_ipc`

Runtime summary tests:

- `BOOT-SUM-01`: `test_runtime_contract_summary_defaults_for_memory_profile`
- `BOOT-SUM-02`: `test_runtime_contract_summary_normalizes_runtime_sections`

---

## Implementation pointers

Primary implementation:

- `src/stream_kernel/config/validator.py`
  - enum sets for bootstrap mode, secret mode, kdf profile;
  - normalization defaults;
  - cross-field precondition check for supervisor mode.
- `src/stream_kernel/app/runtime.py`
  - runtime summary output fields for bootstrap/secret contract.

Test coverage:

- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/stream_kernel/app/test_framework_run.py`

---

## Compatibility and migration notes

Backward-compatibility guarantee in Step A:

- memory/default profile remains valid with no new required sections;
- existing tcp-local profiles remain valid, with deterministic field defaults.

Forward-compatibility posture:

- `bootstrap.mode` and `auth.secret_mode` are explicitly enum-gated, so new
  modes must be added intentionally with tests and docs updates.

---

## Entry criteria for Step B

Step B (bootstrap supervisor API) starts only when all items are true:

- Step A validator and runtime-summary tests are green;
- invariants I1-I4 are enforced;
- this spec is linked from the Phase 3 main plan and web master plan.

---

## Status

Step A status: complete and frozen.
