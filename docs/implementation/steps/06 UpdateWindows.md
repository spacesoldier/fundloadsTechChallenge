# Step Spec

**Step name (registry):** `update_windows`  
**File:** `docs/implementation/steps/06 UpdateWindows.md`  
**Implementation:** [update_windows.py](../../../src/fund_load/usecases/steps/update_windows.py)  
**Responsibility:** Mutate window state based on the `Decision`, according to configured window semantics, while preserving strict input order.

This step is the **only** place where window state is updated:
- daily attempt counters (canonical attempts, regardless of approval)
- daily accepted amount sums (canonical + approved only)
- weekly accepted amount sums (canonical + approved only)
- optional global prime gate usage (canonical + approved + prime only)

This step is stateful and writes through the **WindowWritePort**.

---

## 1. Contract

### Input
- `Decision`
  - includes:
    - `line_no`
    - `customer_id`
    - `day_key`, `week_key`
    - `accepted`
    - `effective_amount`
    - `idem_status`
    - `is_canonical`
    - `is_prime_id`

### Output
- `Decision` (pass-through, unchanged)

### Signature
`(msg: Decision, ctx: Ctx) -> Iterable[Decision]`

**Cardinality:** exactly 1 output per input.

---

## 2. Configuration dependencies

From `docs/implementation/architecture/Configuration spec.md` (newgen):

```yaml
nodes:
  update_windows:
    daily_prime_gate:
      enabled: false                # baseline: false, exp_mp: true
```

---
## 3. Window update rules

### 3.1 Canonical-only mutation

If `msg.is_canonical == false`:
- MUST NOT update any window state.
- Pass the decision through unchanged.

Rationale:
- Replay/conflict rows must not affect later outcomes.

### 3.2 Daily attempts (canonical)

If daily_attempts enabled:
- Increment attempts for `(customer_id, day_key)` by **1** for every canonical decision,  
    regardless of `accepted`.

This matches the requirement:
> “A customer can perform a maximum of 3 load attempts per day, regardless of the amount.”

Note: attempts are “attempts”, not “accepted loads”.

### 3.3 Daily accepted amount (canonical + approved)

If daily_accepted_amount enabled:
- If `accepted == true`:
    - add `effective_amount` to `(customer_id, day_key)` sum
- If declined:
    - no change

### 3.4 Weekly accepted amount (canonical + approved)

If weekly_accepted_amount enabled:
- If `accepted == true`:
    - add `effective_amount` to `(customer_id, week_key)` sum
- If declined:
    - no change

### 3.5 Prime global gate (canonical + approved + prime)

If daily_prime_gate enabled:
- If `accepted == true` AND `is_prime_id == true`:
    - increment `(day_key)` global prime approved counter by 1
- Otherwise:
    - no change

---

## 4. Required write operations (ports)

This step writes via **WindowWritePort**.

### 4.1 Atomicity expectation

Within processing of a single decision:
- window updates for that decision should be applied in a consistent order,
- but full multi-window atomic transactions are not required for the challenge.

However, for determinism in tests:
- updates must be applied synchronously in stream order.

### 4.2 Upsert semantics (conceptual)

Each window write is an “upsert + increment”:
- if row exists → increment
- else → create with initial value

---

## 5. Ordering guarantees

Because the challenge expects decisions in input order, and policies depend on “before” snapshots:
- Steps 05 and 06 must be executed sequentially for a single stream partition.

This step must preserve:
- no reordering of messages,
- no async buffering that changes the effective order.

---

## 6. Invariants (INV-*)

### INV-01: Exactly one output per input

Always emits the same `Decision` it received.

### INV-02: Non-canonical decisions never mutate state

If `is_canonical=false`, no window writes occur.

### INV-03: Daily attempts update is unconditional for canonical

Canonical decisions always increment daily attempts, regardless of acceptance.

### INV-04: Accepted sums update only on approved

Daily/weekly amount sums update only when `accepted=true`.

### INV-05: Prime gate updates only on approved prime canonical

Global prime counter updates only for canonical + approved + prime.

### INV-06: Deterministic state progression

Given the same stream order and decisions, window state after N events is deterministic.

---

## 7. Edge cases to cover (tests)

1. Canonical approved → attempts +1, daily sum +amount, weekly sum +amount
2. Canonical declined → attempts +1, sums unchanged
3. Non-canonical declined (replay/conflict) → no updates at all
4. Approved prime (exp_mp) → prime counter +1
5. Approved non-prime → prime counter unchanged
6. Multiple decisions same customer/day accumulate attempts and sums correctly
7. Week boundary: weekly sum buckets by week_key correctly

---

## 8. Diagnostics and context usage

Optional:
- counters:
    - `ctx.metrics["windows.attempts_writes"]`
    - `ctx.metrics["windows.daily_amount_writes"]`
    - `ctx.metrics["windows.weekly_amount_writes"]`
    - `ctx.metrics["windows.prime_writes"]`
- for debugging, attach lightweight tags:
    - `ctx.tags["day_key"]`, `ctx.tags["week_key"]`

Ctx is not required for correctness.

---

## 9. Test plan (before code)

### Unit tests (pytest)

Use an in-memory fake WindowWritePort that records writes, and a fake store you can query.

Suggested tests:
- `test_windows_update_canonical_approved_updates_all()`
- `test_windows_update_canonical_declined_updates_attempts_only()`
- `test_windows_update_noncanonical_no_updates()`
- `test_windows_update_prime_gate_only_for_prime_approved()`
- `test_windows_update_accumulates_correctly_over_multiple_events()`

Each test asserts:
- output Decision unchanged
- exact set of write operations performed (or exact final state)

---

## 10. Non-goals

- No decision making here (already done in Step 05).
- No snapshot reads here (Step 05 reads “before” snapshots).
- No formatting or output I/O here.
