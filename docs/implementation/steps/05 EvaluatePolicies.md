# Step Spec

**Step name (registry):** `evaluate_policies`  
**File:** `docs/implementation/steps/05 EvaluatePolicies.md`  
**Responsibility:** Produce an acceptance decision for each enriched attempt by evaluating the configured policy pack against current window snapshots and global gates.

This step:
- reads **window state snapshots** (attempt counts, accepted sums, optional global prime gate usage),
- applies policies in a deterministic order,
- emits a `Decision` (accepted/declined) for the current input line,
- does **not** mutate window state (mutations happen in Step 06).

---

## 1. Contract

### Input
- `EnrichedAttempt`
  - includes:
    - `base.base.attempt` (id, customer_id, ts, amount)
    - `base.base.day_key`, `base.base.week_key`
    - `base.idem_status`
    - `features.effective_amount`
    - `features.is_prime_id`

### Output
- `Decision`
  - `line_no, id, customer_id`
  - `accepted: bool`
  - `reasons: tuple[str, ...]` (diagnostic, optional)
  - carry-through for Step 06:
    - `day_key, week_key`
    - `effective_amount`
    - `idem_status`
    - `is_prime_id`
    - `is_canonical` (derived from idem_status)

### Signature
`(msg: EnrichedAttempt, ctx: Ctx) -> Iterable[Decision]`

**Cardinality:** exactly 1 output per input.

---

## 2. Configuration dependencies

From `docs/Configuration Spec.md`:

```yaml
policies:
  pack: "baseline"          # baseline | exp_mp
  evaluation_order:
    - "IDEMPOTENCY"
    - "DAILY_ATTEMPTS"
    - "PRIME_GATE"
    - "DAILY_AMOUNT"
    - "WEEKLY_AMOUNT"
  limits:
    daily_amount: 5000.00
    weekly_amount: 20000.00
    daily_attempts: 3

  prime_gate:
    enabled: false
    global_per_day: 1
    amount_cap: 9999.00

windows:
  daily_attempts:
    enabled: true
    count_all_attempts: true       # canonical attempts count regardless of approval
  daily_accepted_amount:
    enabled: true
  weekly_accepted_amount:
    enabled: true
  daily_prime_gate:
    enabled: false                 # exp_mp only
```

Notes:

- `evaluation_order` is fixed and explicit. We do not allow “if/else soup”.
- Config must be validated at startup.

---

## 3. Required window reads (ports)

This step reads window snapshots via the **WindowReadPort**.

### 3.1 Snapshot inputs

For the attempt:
- `customer_id`
- `day_key`
- `week_key`

### 3.2 Snapshot values required (baseline)

- `day_attempts_before` (integer)
- `day_accepted_amount_before` (Money)
- `week_accepted_amount_before` (Money)

### 3.3 Snapshot values required (exp_mp)

Additionally:
- `prime_approved_count_before` (integer) for `day_key` (global, not per customer)

### 3.4 Missing state defaults

If a window entry is missing:
- attempts_before = 0
- amount_before = 0

---

## 4. Policy evaluation semantics

### 4.1 Canonical vs non-canonical attempts

A `Decision` is always produced, but state mutation is limited later.
Define:
- `is_canonical = (idem_status == CANONICAL)`

Rules:
- If `idem_status == DUP_REPLAY` → decline (reason: `ID_DUPLICATE_REPLAY`)
- If `idem_status == DUP_CONFLICT` → decline (reason: `ID_DUPLICATE_CONFLICT`)
- Canonical continues to normal policy checks.

Rationale:
- Challenge data contains conflicting ids; we treat only first seen id as canonical.

### 4.2 Daily attempt limit

Attempts are counted **per customer per day**.
- Attempt number for current line:
    - `attempt_no = day_attempts_before + 1` (only meaningful if canonical)

Decision rule:
- if canonical and `attempt_no > daily_attempts_limit` → decline (`DAILY_ATTEMPT_LIMIT`)

Important:
- Attempt limit is checked before amount limits (order is explicit).

### 4.3 Prime global gate (exp_mp)

Enabled only when `policies.prime_gate.enabled = true`.

Rules apply only if:
- canonical
- is_prime_id = true

Checks (in this order):

1. Amount cap:
    - if `effective_amount > prime_amount_cap` → decline (`PRIME_AMOUNT_CAP`)
2. Global-per-day gate:
    - if `prime_approved_count_before >= global_per_day` → decline (`PRIME_DAILY_GLOBAL_LIMIT`)

Notes:
- The gate counts **approved prime events** across all clients per UTC day.
- The gate is enforced before daily/weekly amount checks (per configured order).

### 4.4 Daily amount limit (accepted sums)

Only counts **accepted** canonical loads.

Rule:
- if canonical and still approved so far:
    - if `(day_accepted_amount_before + effective_amount) > daily_amount_limit` → decline (`DAILY_AMOUNT_LIMIT`)

### 4.5 Weekly amount limit (accepted sums)

Only counts **accepted** canonical loads.

Rule:
- if canonical and still approved so far:
    - if `(week_accepted_amount_before + effective_amount) > weekly_amount_limit` → decline (`WEEKLY_AMOUNT_LIMIT`)

### 4.6 First-failure vs multi-reason

For this challenge:
- Use **first-failure** semantics: once declined, stop evaluating remaining policies.

Diagnostics (`reasons`) will contain either:
- empty tuple (approved),
- or a single reason code.

(Internally we may still collect more, but first-failure keeps tests stable and predictable.)

---

## 5. Effective amount semantics (Monday multiplier)

This step does not compute `effective_amount`; it trusts Step 04.

Interpretation:
- if config says multiplier applies to amount → limits are constant; effective_amount varies
- if config says multiplier applies to limits → effective_amount equals raw amount; limits are adjusted upstream or inside policy functions

For the challenge we recommend:
- multiplier applies to amount (simpler, matches “counted as double their value”).

---

## 6. Output construction

A `Decision` must carry enough information for Step 06 to mutate state correctly:

Required fields in Decision:
- `line_no`
- `id`, `customer_id`
- `accepted`
- `reasons` (tuple\[str,...\])
- `day_key`, `week_key`
- `effective_amount`
- `idem_status`
- `is_prime_id`
- `is_canonical`

---

## 7. Invariants (INV-*)

### INV-01: Exactly one decision per input

Always emits one `Decision`.

### INV-02: Determinism

For a fixed input order, config, and window snapshots, decisions are deterministic.

### INV-03: Non-canonical never accepted

`DUP_REPLAY` and `DUP_CONFLICT` must always produce `accepted=false`.

### INV-04: Policy order is respected

Evaluation order is exactly as configured; first-failure semantics stop evaluation.

### INV-05: Attempt limit uses “attempt_no = before + 1”

Attempt number includes the current canonical attempt.

### INV-06: Amount limits apply only when still approved

Daily/weekly amount checks only run if not already declined.

---

## 8. Edge cases to cover (tests)

1. Canonical attempt within all limits → approved
2. 4th attempt same day → declined by DAILY_ATTEMPT_LIMIT (even if amount is small)
3. Daily amount would exceed limit by 0.01 → declined
4. Weekly amount would exceed limit → declined
5. Duplicate replay → declined regardless of limits
6. Duplicate conflict → declined regardless of limits
7. Prime id on a day where prime quota already used → declined
8. Prime id amount above cap → declined even if quota unused
9. Monday effective_amount pushes over limit while raw amount would not → declined (exp_mp)

---

## 9. Diagnostics and context usage

Optional metrics:
- `ctx.metrics["decisions.approved"] += 1`
- `ctx.metrics["decisions.declined"] += 1`
- per-reason counters

Ctx is not required for correctness.

---

## 10. Test plan (before code)

### Unit tests (pytest) — policy evaluator

Use an in-memory fake WindowReadPort returning controlled snapshots.

Suggested tests:
- `test_policy_baseline_approved_happy_path()`
- `test_policy_decline_daily_attempt_limit()`
- `test_policy_decline_daily_amount_limit()`
- `test_policy_decline_weekly_amount_limit()`
- `test_policy_decline_duplicate_replay()`
- `test_policy_decline_duplicate_conflict()`
- `test_policy_prime_gate_amount_cap()`
- `test_policy_prime_gate_global_quota()`
- `test_policy_monday_multiplier_effective_amount()`

Each test asserts:
- exactly one Decision
- accepted flag correct
- reasons tuple equals expected single reason (or empty)

---

## 11. Non-goals

- No window mutations (Step 06).
- No parsing (Step 01).
- No time key computation (Step 02).
- No idempotency classification (Step 03).
- No feature computation (Step 04).
