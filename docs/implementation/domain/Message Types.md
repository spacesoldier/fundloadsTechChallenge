# Intro

This document defines the immutable **message types** used by the flow.
Messages represent domain events and their derived forms as they move left-to-right through the system.

Key rule:

> **Messages are immutable value objects.**  
> Any enrichment produces a new message rather than mutating an existing one.

Mutable execution metadata belongs to `Ctx` (context), not to messages.

---

## 1. Design goals

- **Explicit contracts** at step boundaries
- **No implicit schemas** (even partial typing beats implicit dicts)
- **Deterministic behavior** and easy replay
- **Ruby-friendly mental model** (pipeline of transformations)

---

## 2. Common conventions

### 2.1 Identity and ordering
- `line_no` is the canonical ordering key for the entire flow.
- We preserve input order strictly; `line_no` is carried through all message types.

### 2.2 Original fields
- `id` and `customer_id` are preserved as **strings** exactly as provided in input.
- We may also store parsed numeric forms internally (e.g. `id_num`) where needed.

### 2.3 Raw payload retention
- Each parsed attempt may retain a `raw` payload (mapping) for diagnostics.
- Raw payload must never be required for correctness.

---

## 3. Type definitions (conceptual)

The following are conceptual schemas. The implementation will use Python `@dataclass(frozen=True, slots=True)` (or equivalent).

---

## 4. Core message types

### 4.1 RawLine
Represents one physical input line.

Fields:
- `line_no: int` — 1-based or 0-based (must be documented; recommended 1-based for human readability)
- `raw_text: str` — the raw line contents

Guarantees:
- `line_no` is monotonically increasing
- `raw_text` is non-empty (unless the input contains empty lines; if so, parsing rules must define behavior)

---

### 4.2 LoadAttempt
Represents a parsed and normalized fund load attempt.

Fields:
- `line_no: int`
- `id: str`
- `customer_id: str`
- `amount: Money`
- `ts: datetime` — timezone-aware, normalized to UTC
- `raw: Mapping[str, Any]` (optional, for diagnostics)

Guarantees:
- `id` and `customer_id` are present and non-empty strings
- `amount` is normalized and valid per `Money` semantics
- `ts` is valid and UTC

---

### 4.3 AttemptWithKeys
A load attempt with derived time bucketing keys.

Fields:
- `attempt: LoadAttempt`
- `day_key: date` — UTC day derived from `attempt.ts`
- `week_key: WeekKey` — derived from config (calendar vs rolling)

Guarantees:
- `day_key` is deterministic for the same `ts` and timezone rules
- `week_key` is deterministic for the same `ts` and week definition

---

### 4.4 IdempotencyClassifiedAttempt
An attempt classified by idempotency status.

Fields:
- `base: AttemptWithKeys`
- `idem_status: IdemStatus`
- `fingerprint: str` (optional but recommended) — stable hash of canonical payload fields
- `canonical_line_no: int` (optional) — where the canonical record first appeared

`IdemStatus` enum values:
- `CANONICAL`
- `DUP_REPLAY` — same id + same fingerprint
- `DUP_CONFLICT` — same id + different fingerprint

Guarantees:
- Every input attempt receives exactly one idempotency classification.
- Only `CANONICAL` attempts are eligible to mutate window state.

---

### 4.5 Features
Derived features required for policies.

Fields (baseline subset):
- `risk_factor: float` — default `1.0`
- `effective_amount: Money` — `amount * risk_factor` (or per config)
- `is_prime_id: bool` — only meaningful if prime gate enabled

Optional / future:
- additional feature flags and scores

Guarantees:
- Features are derived deterministically from the attempt + config.
- Feature derivation must not mutate the original attempt.

---

### 4.6 EnrichedAttempt
An attempt bundled with features.

Fields:
- `base: IdempotencyClassifiedAttempt`
- `features: Features`

Guarantees:
- `features` correspond to `base` attempt fields and config

---

### 4.7 Decision
The policy outcome for a given input line.

Fields:
- `line_no: int`
- `id: str`
- `customer_id: str`
- `accepted: bool`
- `reasons: tuple[str, ...]` (optional diagnostics; not required in final output)
- `idem_status: IdemStatus`
- `day_key: date`
- `week_key: WeekKey`
- `effective_amount: Money` (recommended to carry forward for window updates)
- `is_canonical: bool` (redundant but convenient)
- `is_prime_id: bool` (for prime gate window updates)

Guarantees:
- Exactly one `Decision` exists per input line.
- `accepted` is deterministic given input order, config, and window state.
- Window update semantics are derivable from decision fields without needing hidden state.

---

### 4.8 OutputRow
The final output row.

Fields:
- `id: str`
- `customer_id: str`
- `accepted: bool`

Guarantees:
- OutputRow is a direct projection of Decision
- Output order matches ascending `line_no`

---

## 5. Supporting value objects

### 5.1 Money
Represents a currency amount.

Recommended representation:
- internal storage as integer cents (minor units), OR
- fixed decimal with explicit rounding

Fields (conceptual):
- `currency: str` — fixed `"USD"` for this challenge
- `cents: int` OR `amount: Decimal`

Guarantees:
- non-negative in this domain
- stable arithmetic (no float accumulation)

---

### 5.2 WeekKey
Represents a weekly bucket key.

Variants:
- `CalendarWeekKey(year, week_no, week_start)`  
- `RollingWindowKey(anchor_day, days=7)` (optional, if enabled)

Guarantees:
- derived deterministically from timestamp + config

---

## 6. Reason codes (diagnostics)

Reason codes are optional for the challenge output, but useful for tests and debugging.

Examples:
- `PARSE_ERROR`
- `ID_DUPLICATE_REPLAY`
- `ID_DUPLICATE_CONFLICT`
- `DAILY_ATTEMPT_LIMIT`
- `DAILY_AMOUNT_LIMIT`
- `WEEKLY_AMOUNT_LIMIT`
- `PRIME_AMOUNT_CAP`
- `PRIME_DAILY_GLOBAL_LIMIT`

Reason codes are specified in `docs/domain/Reason Codes.md`.

---

## 7. Notes on immutability and evolution

- Messages may evolve over time, but step contracts must remain explicit.
- Adding fields is allowed; removing or renaming fields must be reflected in specs and tests.

---
