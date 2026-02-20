# File adapter transport model TDD plan

## Goal

Implement framework-level file transport model with explicit format hint, typed
transport records, and deterministic ordering semantics that remain valid for
both sync and future async runners.

Target direction:

- remove text-only assumptions from platform file ingress;
- keep domain conversion in project bridge nodes;
- preserve/reconstruct order with transport-level sequence (`seq`).

## Scope

In scope:

- ingress format hint (`text/jsonl`, `text/plain`, `application/octet-stream`);
- transport records (`TextRecord`, `ByteRecord`) and ordering metadata;
- bridge adaptation path in `fund_load` (`transport record` -> `RawLine`);
- tests for sequential and future parallel-safe ordering semantics.

Out of scope in this plan:

- full parallel reorder sink runtime implementation;
- HTTP/WebSocket adapters (covered by separate network expansion plan).

## Current baseline

- Transport records are explicit in framework (`ByteRecord`, `TextRecord`).
- Ingress supports format hint (`text/jsonl`, `text/plain`, `application/octet-stream`).
- `IngressLineBridge` accepts both transport record types and maps `seq -> RawLine.line_no`.
- End-to-end behavior remains deterministic and green.

## Stages (TDD-first)

1. Stage A: Formalize transport records
- [x] Add framework transport models:
  - `TextRecord(text: str, seq: int | None, source: str, encoding: str = "utf-8")`
  - `ByteRecord(payload: bytes, seq: int | None, source: str)`
- [x] Keep compatibility alias for current `StreamLine` only where needed by tests.
- [x] Update contract tests to assert framework nodes/adapters consume/emit transport models.
- Exit criteria:
  - no file adapter contract depends on project domain models;
  - all adapter/bridge tests green.

2. Stage B: Config format hint support
- [x] Extend config validation for `adapters.<role>.settings.format`:
  - allow `text/jsonl`, `text/plain`, `application/octet-stream`;
  - default to `text/jsonl` for current challenge behavior (in ingress adapter).
- [x] Add validator tests for unknown/invalid format values.
- [x] Wire `ingress_file` adapter factory to instantiate proper line/byte parser mode.
- Exit criteria:
  - validator fails on unknown format;
  - ingress emits expected transport model by format.

3. Stage C: Bridge behavior by transport model
- [x] Update `fund_load` ingress bridge to consume `TextRecord` (or decode from `ByteRecord` mode explicitly).
- [x] Keep line/sequence mapping in bridge, not in adapter semantics.
- [x] Add tests for:
  - valid UTF-8 decode path;
  - decode failure classification policy (strict error vs fallback).
- Exit criteria:
  - domain `RawLine` creation remains project-owned;
  - bridge tests cover sequence mapping and decode behavior.

4. Stage D: Ordering contract hardening
- [x] Make `seq` semantics explicit in runtime docs/tests:
  - monotonic for single-reader ingress;
  - required for deterministic merge in parallel path.
- [x] Add execution-level characterization tests for out-of-order completion data shape
      (without full reorder engine yet).
- [x] Add strict preflight/runtime guard for missing `seq` where ordered sink mode is requested.
- Exit criteria:
  - ordering expectations are test-backed and documented;
  - no hidden dependency on domain `line_no`.

5. Stage E: Documentation sync
- [x] Update `Ports and adapters model.md` with final transport record names and config examples.
- [x] Update `File adapters and ordering model.md` with implemented status markers.
- [x] Link related test files in doc sections.
- Exit criteria:
  - docs match code behavior;
  - full regression green.

## TDD test matrix (starter IDs)

- `FILE-TM-01`: ingress emits `ByteRecord` for `application/octet-stream`.
- `FILE-TM-02`: ingress emits `TextRecord` for `text/jsonl`.
- `FILE-TM-03`: unknown `format` fails validation.
- `FILE-TM-04`: bridge maps transport `seq` to domain ordering field deterministically.
- `FILE-TM-05`: bridge decode failure handled per configured strictness (`strict`/`replace`).
- `FILE-TM-06`: runtime trace metadata includes transport `seq`, not project-specific `line_no`.
- `FILE-TM-07`: e2e baseline output remains byte-for-byte identical to reference.

## Execution order recommendation

1) Stage A -> 2) Stage B -> 3) Stage C (done) -> 4) Stage D -> 5) Stage E.

Run full regression after each stage:

- `poetry run pytest -q`
