# Step Spec

**Step name (registry):** `parse_load_attempt`  
**File:** `docs/implementation/steps/01 ParseLoadAttempt.md`  
**Implementation:** [parse_load_attempt.py](../../../src/fund_load/usecases/steps/parse_load_attempt.py)  
**Responsibility:** Parse one raw input line into a normalized `LoadAttempt` message (or emit an immediate declined `Decision` on irrecoverable parse/normalization errors).

This step is the **only** place where raw line text is interpreted as JSON and normalized into domain types.

---

## 1. Contract

### Input
- `RawLine`
  - `line_no: int`
  - `raw_text: str`

### Output (one of)
- `LoadAttempt` (normalized)
- `Decision` (declined, when the input line cannot be parsed/normalized)

### Signature
`(msg: RawLine, ctx: Ctx) -> Iterable[LoadAttempt | Decision]`

**Cardinality:**
- MUST emit exactly one output message per input message:
  - either one `LoadAttempt`, or one declined `Decision`.

---

## 2. Configuration dependencies

This step is intentionally configuration-light.

It may depend on **domain parsing configuration** (from `docs/Configuration Spec.md`):
- `domain.money.currency` (default: USD)
- `domain.money.rounding` (if decimals are accepted)
- optional: accepted money formats list (if configurable)

If parsing config is invalid → fail fast at startup (handled by config validation, not by this step).

---

## 3. Normalization rules

### 3.1 Required input fields
The raw JSON object must contain:
- `id` (string or number; stored as string)
- `customer_id` (string or number; stored as string)
- `load_amount` (string; multiple accepted formats, see below)
- `time` (ISO8601 string, with timezone; expected `Z`)

If any required field is missing → emit declined `Decision` with reason `PARSE_ERROR` or `SCHEMA_ERROR`.

### 3.2 id and customer_id normalization
- Accept JSON string or number.
- Normalize to string via:
  - if numeric: convert to base-10 string without decimals
  - if string: keep as-is, but trim whitespace
- Validate: must match `^\d+$` after trimming.

Violations → declined `Decision` (`INVALID_ID` or `INVALID_CUSTOMER_ID`).

### 3.3 Timestamp normalization
- Parse `time` as timezone-aware datetime.
- Normalize to UTC.
- If parsing fails → declined `Decision` (`INVALID_TIME`).

### 3.4 Money normalization
Input field is `load_amount` and may appear in mixed formats (dataset reality):
- `$1234.00`
- `USD1234.00`
- `USD$1234.00`

Normalization rules:
1) Strip whitespace
2) Remove optional leading currency token `USD` (case-sensitive or case-insensitive — choose and document)
3) Remove optional `$`
4) Parse remaining numeric as decimal with exactly 2 fractional digits (or allow more then round per config)

Validate:
- non-negative
- must be representable in internal money type

Violations → declined `Decision` (`INVALID_AMOUNT`).

---

## 4. Output construction

### 4.1 LoadAttempt (success path)
Construct `LoadAttempt`:
- `line_no` from `RawLine.line_no`
- `id` (string)
- `customer_id` (string)
- `amount` (Money)
- `ts` (UTC datetime)
- `raw` (optional: parsed dict)

### 4.2 Decision (failure path)
If parsing/normalization fails:
- Construct a declined `Decision` with:
  - `line_no`, `id` and `customer_id` when available; otherwise fallback to empty string (must be explicit)
  - `accepted = false`
  - `reasons = (<reason_code>,)`
  - `idem_status = CANONICAL` or a dedicated status like `N/A` (choose one and keep consistent)
  - minimal keys (`day_key/week_key`) may be omitted here if downstream expects them; alternatively set to `None` and allow later steps to skip

Recommended approach:
- Use a dedicated `Decision` variant that does not require time keys when parse fails, and ensure downstream routing handles it.

---

## 5. Invariants (INV-*)

Each invariant must have unit tests.

### INV-01: Exactly one output per input
For every `RawLine`, step emits exactly one message.

### INV-02: On success, all normalized fields are valid
If output is `LoadAttempt`:
- `id` matches `^\d+$`
- `customer_id` matches `^\d+$`
- `ts` is UTC-aware
- `amount` is valid Money and non-negative

### INV-03: On failure, output is a declined Decision
If parsing fails:
- output is `Decision(accepted=false)`
- `reasons` contains a single canonical reason code (first-failure semantics)

### INV-04: Money formats are handled as specified
All supported formats parse to the same normalized amount:
- `$431.04` == `USD431.04` == `USD$431.04`

---

## 6. Edge cases to cover (tests)

1) Valid JSON with numeric `id` / `customer_id`
2) Valid JSON with string `id` / `customer_id` + whitespace
3) Amount formats: `$…`, `USD…`, `USD$…`
4) Invalid JSON (broken line)
5) Missing fields
6) Invalid timestamp format
7) Negative amount (if appears) → declined
8) Amount with wrong decimals (`$12.3`, `$12.345`) → decide strictness and test it
9) Empty line / whitespace-only line (if input contains it)

---

## 7. Diagnostics and context usage

- On failures, append a short message into `ctx.errors` (optional).
- Keep `ctx` optional for correctness: tests should validate outputs without relying on context.

---

## 8. Test plan (before code)

### Unit tests (pytest)
- `test_parse_success_minimal()`
- `test_parse_amount_formats_equivalent()`
- `test_parse_invalid_json_declined()`
- `test_parse_missing_field_declined()`
- `test_parse_invalid_time_declined()`
- `test_parse_invalid_amount_declined()`
- `test_parse_invalid_id_declined()`

All tests must assert:
- output cardinality = 1
- correct message type
- correct normalized values or correct reason codes

---

## 9. Non-goals

- No policy evaluation here.
- No window/state access here.
- No idempotency classification here.
- No file I/O here (input is already `RawLine`).

---
