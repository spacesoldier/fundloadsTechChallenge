
This document defines **stable, machine-readable reason codes** used by the decision engine for diagnostics, testing, and (future) auditability.

**Important:** The challenge output does **not** include reasons. Reasons exist for:
- deterministic testing (asserting “why” a decision happened),
- internal logging / audit trails,
- future productization (e.g., explainability surfaces).

Unless stated otherwise, the engine uses **first-failure semantics**:
- exactly **one** primary reason is recorded for a declined canonical attempt.

---

## 1. Naming and conventions

- Format: `UPPER_SNAKE_CASE`
- Meaning: the first violated invariant / rule that caused the decline
- Scope: codes are stable across configs; configs only change which codes can be produced

Recommended internal structure:
- keep `reason: ReasonCode | None` (single) as the canonical field
- optionally keep `reasons: tuple[ReasonCode, ...]` if multi-reason is ever needed

---

## 2. Idempotency reasons

### `ID_DUPLICATE_REPLAY`
**Meaning:** A repeated attempt with the same `id` and identical payload (fingerprint match) was seen after a canonical event.  
**When used:** `idem_status == DUP_REPLAY`  
**Window impact:** none (non-canonical)

### `ID_DUPLICATE_CONFLICT`
**Meaning:** A repeated attempt with the same `id` but different payload (fingerprint mismatch) was seen after a canonical event.  
**When used:** `idem_status == DUP_CONFLICT`  
**Window impact:** none (non-canonical)

---

## 3. Volume / velocity limit reasons

### `DAILY_ATTEMPT_LIMIT`
**Meaning:** Customer exceeded the maximum number of load attempts for a UTC day.  
**Policy:** `(attempts_before + 1) > daily_attempt_limit`  
**Window impact:** attempts still increment (canonical-only), but the attempt is declined.

### `DAILY_AMOUNT_LIMIT`
**Meaning:** Customer exceeded the daily accepted load amount limit for a UTC day.  
**Policy:** `(day_accepted_amount_before + effective_amount) > daily_amount_limit`  
**Window impact:** attempts increment; accepted amount does not.

### `WEEKLY_AMOUNT_LIMIT`
**Meaning:** Customer exceeded the weekly accepted load amount limit for a UTC week.  
**Policy:** `(week_accepted_amount_before + effective_amount) > weekly_amount_limit`  
**Window impact:** attempts increment; accepted amount does not.

---

## 4. Prime gate reasons (experimental policy pack)

These reasons are only possible if the **Prime Gate** feature/policy is enabled.

### `PRIME_AMOUNT_CAP`
**Meaning:** Prime-id attempt exceeded the maximum allowed amount for prime IDs.  
**Policy:** `effective_amount > prime_amount_cap`

### `PRIME_DAILY_GLOBAL_LIMIT`
**Meaning:** Daily global quota of approved prime-id attempts has already been used.  
**Policy:** `prime_approved_count_before >= prime_global_per_day`  
**Scope:** global across all customers, per UTC day.

---

## 5. Parsing / normalization reasons (optional)

These are optional and depend on whether we treat malformed input as a hard error or a declined attempt.

### `INPUT_PARSE_ERROR`
**Meaning:** The input line cannot be parsed as valid JSON or lacks required fields.

### `INVALID_TIMESTAMP`
**Meaning:** `time` is not ISO-8601 with timezone, or cannot be normalized to UTC.

### `INVALID_AMOUNT_FORMAT`
**Meaning:** `load_amount` cannot be normalized into Money (e.g., unexpected currency token format).

### `INVALID_ID_FORMAT`
**Meaning:** `id` or `customer_id` is not numeric (if numeric-only is assumed).

> Note: For the provided dataset we expect parsing to succeed. If these codes are implemented, define whether they produce `accepted=false` or terminate the run.

---

## 6. Canonical reason precedence (first-failure order)

Recommended default precedence (matches our policy evaluation order):

1) `ID_DUPLICATE_REPLAY` / `ID_DUPLICATE_CONFLICT`  
2) `DAILY_ATTEMPT_LIMIT`  
3) `PRIME_AMOUNT_CAP`  
4) `PRIME_DAILY_GLOBAL_LIMIT`  
5) `DAILY_AMOUNT_LIMIT`  
6) `WEEKLY_AMOUNT_LIMIT`  

This ensures:
- idempotency is handled before all business rules,
- attempt count is enforced regardless of amounts,
- prime gate is a “front-door” restriction,
- amount limits apply only when all previous checks passed.

---

## 7. Mapping table (quick reference)

| Code                     | Category       | Applies to | Short description |
|--------------------------|----------------|------------|-------------------|
| ID_DUPLICATE_REPLAY      | Idempotency    | non-canon  | same id, same payload |
| ID_DUPLICATE_CONFLICT    | Idempotency    | non-canon  | same id, different payload |
| DAILY_ATTEMPT_LIMIT      | Velocity       | canonical  | > N attempts/day |
| DAILY_AMOUNT_LIMIT       | Velocity       | canonical  | > $/day accepted sum |
| WEEKLY_AMOUNT_LIMIT      | Velocity       | canonical  | > $/week accepted sum |
| PRIME_AMOUNT_CAP         | Prime gate     | canonical  | prime id amount cap |
| PRIME_DAILY_GLOBAL_LIMIT | Prime gate     | canonical  | only one prime/day globally |
| INPUT_PARSE_ERROR        | Input          | line       | invalid JSON/fields |
| INVALID_TIMESTAMP        | Input          | line       | cannot parse/normalize time |
| INVALID_AMOUNT_FORMAT    | Input          | line       | cannot normalize amount |
| INVALID_ID_FORMAT        | Input          | line       | invalid id/customer_id |

---

## 8. Stability guarantees

- Reason codes are part of the **public internal contract** of the engine.
- New codes may be added, but existing codes must not change meaning.
- Tests should assert the code for representative scenarios.

---
