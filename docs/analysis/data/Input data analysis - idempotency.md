# Input Data Analysis and Idempotency Handling

## 1. Purpose
This document records findings from analyzing the provided input dataset and explains how the solution handles data quality issues—specifically, **duplicate IDs**—in a deterministic and audit-friendly way.

The overall goal is to ensure that the adjudication results do not depend on accidental input duplication and that velocity windows are not distorted by repeated deliveries.

## 2. Observations from the dataset

### 2.1 NDJSON format and streaming assumption
The provided file is in a line-delimited JSON format (NDJSON / JSON Lines), where each line represents an independent fund load attempt.

This format naturally suggests **streaming processing**:
- read line-by-line,
- evaluate sequentially,
- produce output per event without needing to load the entire file into memory.

### 2.2 Duplicate `id` values exist
During input inspection, we found that the dataset contains **repeated values of `id`**, and importantly:
- at least some duplicates have **different `load_amount` values**.

This is a strong indication that the dataset intentionally includes a “sharp edge” to test robustness:
- whether the candidate assumes `id` is globally unique,
- whether the solution remains deterministic when such duplicates appear,
- whether velocity limits can be exploited/biased by repeated delivery.

From a system perspective, duplicates may represent:
- repeated delivery of the same event (at-least-once delivery semantics),
- retries or replays,
- incorrect upstream behavior,
- or an explicit adversarial test.

## 3. Why naive uniqueness constraints are insufficient
A naive approach is to enforce `UNIQUE(id)` at ingestion time and fail the load on duplicates. This is problematic because:
- it can reject valid datasets and hide the real handling logic,
- it does not distinguish between benign replays and conflicting duplicates,
- it can stop analysis early rather than producing complete adjudication output,
- it does not provide an explicit decision rationale for duplicates.

In real systems, deduplication is usually a **domain behavior**, not a database accident.

Therefore, duplicates are handled as part of the decision pipeline.

## 4. Idempotency: what problem we solve
In stateful adjudication engines (velocity windows, global quotas), duplicates must not:
- consume daily/weekly limits multiple times,
- inflate total loaded amounts,
- change the outcome of unrelated future decisions.

This is the classic **idempotency** requirement:
> multiple deliveries of the “same request” must result in a stable outcome and must not mutate state more than once.

Additionally, when the same id is used with a different payload, it becomes a **conflict**:
- either the upstream is broken,
- or the data is adversarial,
- or it represents a correction flow (which is out of scope for this challenge).

In a money/regulatory context, the safe strategy is to reject conflicts deterministically.

## 5. Chosen strategy: Idempotency Gate

### 5.1 Overview
The solution introduces an explicit pipeline stage called **Idempotency Gate**, placed early in the pipeline:
- after parsing and basic field validation,
- before window aggregation and policy evaluation.

Its job is to classify repeated ids and prevent duplicates from affecting state.

### 5.2 Definitions
- **Idempotency key**: `id` from input.
- **Replay**: repeated `id` with the same payload.
- **Conflict**: repeated `id` with a different payload (e.g., different amount).

### 5.3 Fingerprint-based comparison
To detect replay vs conflict, the gate computes a **payload fingerprint** from normalized fields (excluding `id`):
- `customer_id`
- `time`
- `load_amount` (normalized to numeric scale 2)

Excluding `id` is critical: `id` itself cannot distinguish replay from conflict because it is identical in both cases.

### 5.4 Decision rules
For each incoming event in stream order:

1) **First occurrence of `id`**
- process normally through modifiers → window snapshot → policy engine
- obtain a final decision (accepted/rejected)
- store `fingerprint + decision` under this `id`

2) **Subsequent occurrence of the same `id`**
- compute fingerprint
- if fingerprint matches stored fingerprint → **Replay**
  - return the **same decision** as the first occurrence (idempotent replay)
  - bypass window aggregation and policy evaluation
- if fingerprint differs → **Conflict**
  - reject deterministically with a dedicated reason (e.g. `DUPLICATE_ID_CONFLICT`)
  - bypass window aggregation and policy evaluation

### 5.5 Why first occurrence is processed normally in streaming mode
In streaming mode we cannot know whether an event will be duplicated in the future.
Therefore:
- the first occurrence must be treated as a real request,
- the system remains correct by ensuring subsequent duplicates do not affect state.

This is the standard pattern for at-least-once delivery environments.

## 6. Interaction with velocity windows (state safety)
Velocity windows maintain state (daily/weekly totals, attempt counts, global quotas).
To avoid double-counting:
- **windows are updated only on accepted events**
- duplicates/conflicts never reach the window commit stage

This yields deterministic outcomes and prevents “limit manipulation” via repeated input delivery.

## 7. Scope statement
This approach deliberately does not implement “correction events” or “replace/cancel” semantics.
Those require a ledger-like model (versioning, reversals, compensations) and are out of scope for the current challenge.

The chosen behavior (replay = same outcome, conflict = reject) is safe, deterministic, and aligned with typical payment/regulatory constraints.

## 8. Practical notes for reviewers
- Duplicate `id` handling is explicit and testable.
- Replay vs conflict classification is deterministic.
- Window state integrity is guaranteed by commit-on-accept semantics.
- The approach matches how idempotency keys are used in real payment APIs.
