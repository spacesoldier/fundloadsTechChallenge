
This document defines the **time** and **money** rules used by the decision engine.
The goal is determinism, auditability, and consistent interpretation across all steps.

---

## 1. Time semantics

### 1.1 Input timestamp format
- `time` is expected to be an ISO-8601 timestamp **with timezone**.
- Example: `2000-01-01T00:00:00Z` where `Z` means UTC.

**Normalization rule**
- All timestamps are parsed as timezone-aware datetimes and **normalized to UTC**.
- After normalization, the engine uses UTC consistently for:
  - weekday logic (e.g., Monday multiplier),
  - day windows,
  - week windows.

If a timestamp is missing timezone info:
- preferred: treat as invalid input (`INVALID_TIMESTAMP`)
- alternative (only if explicitly chosen): assume UTC
- the chosen behavior must be consistent and documented.

### 1.2 Weekday semantics (Monday)
- “Monday” is evaluated using the **UTC weekday** of the normalized timestamp.
- No “customer locale” or “US state timezone” is applied in this challenge.

### 1.3 Day window semantics
A **day** is defined as:
- UTC date boundary: `00:00:00Z` to `23:59:59.999...Z`

Keys:
- `day_key_utc = YYYY-MM-DD` (UTC date of the event)

Windows:
- Daily attempt counters and daily accepted-amount sums use `day_key_utc`.

### 1.4 Week window semantics
A **week** is defined as a calendar week in UTC.

Default:
- ISO-like week start: **Monday 00:00 UTC**
- Key is computed as a stable week anchor:
  - recommended representation: `week_start_date_utc` (YYYY-MM-DD of Monday)

Keys:
- `week_key_utc = YYYY-MM-DD` of week start (Monday)

> Note: The original dataset-driven reference output and tests must match the chosen week definition. If a different week definition is used (rolling 7-day window, Sunday start), it must be explicitly configured and recomputed.

### 1.5 Rolling vs fixed windows (terminology)
Two styles exist conceptually:

- **Fixed calendar window** (recommended here):
  - aligned to UTC boundaries (day/week)
  - predictable and easy to audit
- **Rolling window**:
  - width-based window from “now” backwards (e.g., last 24h / last 7d)
  - more complex and typically requires timestamp-indexed state

For this challenge we use **fixed calendar windows** only.

Suggested terminology for the opposite of “rolling”:
- `fixed`
- `calendar`
- `anchored`

Recommended word: **`fixed`** (or **`calendar`** if you want to emphasize boundary alignment).

---

## 2. Money semantics

### 2.1 Input amount formats
The dataset may contain any of the following strings:

- `$1234.00`
- `USD1234.00`
- `USD$1234.00`

Normalization rule:
- Extract a numeric amount with exactly 2 decimal places.
- Currency is assumed to be USD for this challenge.

If currency codes differ in future:
- the parser must validate and either reject or support multi-currency explicitly.

### 2.2 Money type
Internally, use an explicit Money representation. Two acceptable approaches:

**A) Decimal dollars**
- store as `Decimal` with 2 decimals (quantized)
- simplest to read, safe from float errors

**B) Integer cents**
- store as `int` cents (e.g., `$12.34` → `1234`)
- simplest arithmetic and comparisons
- formatting converts back to dollars

Recommended for predictability and avoiding rounding surprises:
- **integer cents** internally.

### 2.3 Rounding rules
When normalizing from string:
- parse to cents exactly (no binary floats)
- if input has more than 2 decimals:
  - preferred: reject (`INVALID_AMOUNT_FORMAT`)
  - alternative: round half-up, but only if specified

For this dataset:
- amounts are expected to have 2 decimals.

### 2.4 Monday multiplier and effective amount
The “Mondays are counted as double their value” rule is interpreted as:

- define `risk_factor` (a multiplier, default 1.0)
- if Monday feature enabled: Monday has `risk_factor = 2.0`
- define `effective_amount = amount * risk_factor`

This means:
- daily/weekly limits compare against **effective sums**,
- attempt count remains unaffected.

Alternative semantic (not used by default):
- keep `effective_amount = amount`
- instead adjust limits by dividing by risk_factor
This may be offered as a configuration option, but must not be the default unless tests are recomputed.

### 2.5 Comparisons against limits
All limit checks use strict “greater than” semantics:

- Decline if projected sum **exceeds** the limit:
  - `(current_sum + effective_amount) > limit`

If exactly equal to limit:
- allowed (approved)

---

## 3. Derived keys and fields (summary)

Given a parsed event:

- `ts_utc`: timestamp normalized to UTC
- `day_key_utc`: UTC date of `ts_utc`
- `week_key_utc`: UTC week anchor date (Monday)
- `amount_cents`: normalized integer cents
- `risk_factor`: 1 or Monday multiplier
- `effective_amount_cents`: `amount_cents * risk_factor`

---

## 4. Determinism guarantees

- Timezone normalization ensures stable weekday and bucket assignment.
- Integer cents avoids float instability.
- Fixed calendar windows avoid ambiguous rolling boundaries.

Given the same input order and config, the engine must produce identical output.

---
