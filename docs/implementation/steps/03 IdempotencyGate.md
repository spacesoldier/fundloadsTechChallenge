# Step Spec

**Step name (registry):** `idempotency_gate`  
**File:** `docs/implementation/steps/03 IdempotencyGate.md`  
**Responsibility:** Enforce deterministic idempotency semantics for repeated `id` values in the input stream.

This dataset contains duplicate `id`s, including **conflicting duplicates** (same id, different payload).
This step must:
- classify each event as CANONICAL / DUP_REPLAY / DUP_CONFLICT,
- ensure downstream state updates are applied only for canonical events,
- preserve strict input order and 1:1 output generation later.

This is a **stateful step**, but its state is local to the running process (an in-memory id registry).

---

## 1. Contract

### Input
- `AttemptWithKeys`
  - `attempt: LoadAttempt`
  - `day_key`, `week_key`

### Output
- `IdempotencyClassifiedAttempt`
  - `base: AttemptWithKeys`
  - `idem_status: IdemStatus` (`CANONICAL | DUP_REPLAY | DUP_CONFLICT`)
  - `fingerprint: str`
  - `canonical_line_no: int`

### Signature
`(msg: AttemptWithKeys, ctx: Ctx) -> Iterable[IdempotencyClassifiedAttempt]`

**Cardinality:** exactly 1 output per input.

---

## 2. Configuration dependencies

From `docs/Configuration Spec.md`:

```yaml
idempotency:
  mode: "canonical_first"               # only supported mode for this challenge
  on_conflict: "decline"                # downstream decision policy
  decision_for_noncanonical: "decline"  # downstream decision policy
```

Only `canonical_first` is supported here:
- the earliest occurrence (by input order / `line_no`) is canonical.

`on_conflict` and decision details are not enforced here; this step only classifies.

---

## 3. Definitions

### 3.1 Canonical

The **first** occurrence of a given `id` in input order.

### 3.2 Duplicate replay

A later occurrence of the same `id` with an **identical fingerprint** to the canonical record.

### 3.3 Duplicate conflict

A later occurrence of the same `id` with a **different fingerprint** than the canonical record.

---

## 4. Fingerprint computation

Fingerprint must be stable and deterministic and must reflect the payload fields that define “sameness”.

### 4.1 Recommended fingerprint fields

Use the normalized values from `LoadAttempt`:

- `id` (string)
- `customer_id` (string)
- `amount` (normalized Money, e.g. cents)
- `ts` (UTC timestamp, ISO string or epoch)

Suggested canonical string (example):  
`"{id}|{customer_id}|{amount_cents}|{ts_iso}"`

Fingerprint algorithm:
- `sha256(canonical_string).hexdigest()` (or other stable hash)

### 4.2 Why not include derived keys?

Do NOT include `day_key/week_key` in the fingerprint:
- they are derived from `ts` anyway,
- they would make the fingerprint dependent on configuration.

Fingerprint must depend on **domain payload**, not on configured window semantics.

---

## 5. Internal state model

This step maintains a local registry keyed by `id`:

For each seen `id`, store:
- `fingerprint` (canonical fingerprint)
- `canonical_line_no`
- optionally: canonical payload summary (for debugging)

Space complexity:
- O(number of distinct ids in dataset)  
    This is acceptable for the challenge and for typical batch file sizes.

---

## 6. Output construction logic

Given an input `AttemptWithKeys msg`:
1. Compute `fp = fingerprint(msg.attempt)`
2. Lookup `id = msg.attempt.id` in registry
3. If not found:
    - insert `{id -> (fp, msg.attempt.line_no)}`
    - emit `IdempotencyClassifiedAttempt(..., CANONICAL, fp, canonical_line_no=line_no)`
4. If found:
    - if `fp == stored_fp` → emit `DUP_REPLAY` with stored canonical_line_no
    - else → emit `DUP_CONFLICT` with stored canonical_line_no

---

## 7. Invariants (INV-*)

Each invariant must have unit tests.

### INV-01: Exactly one output per input

Always emits one classified message.

### INV-02: First seen id is canonical

For each `id`, exactly one event is classified as `CANONICAL`, and it is the earliest by `line_no`.

### INV-03: Replay vs conflict classification is correct

For a given `id`:
- identical payload → `DUP_REPLAY`
- differing payload → `DUP_CONFLICT`

### INV-04: Classification does not depend on config

Given the same normalized payload, classification is stable regardless of week/day key config.

### INV-05: Registry state is monotonic

Once an `id` is registered, its canonical fingerprint never changes.

---

## 8. Edge cases to cover (tests)

1. No duplicates at all → all CANONICAL
2. One duplicate with identical payload → second is DUP_REPLAY
3. One duplicate with different amount → DUP_CONFLICT
4. Multiple duplicates:
    - canonical, replay, conflict, replay — classification uses canonical fingerprint
5. Same `id` but different customer_id → conflict
6. Same `id` but timestamp differs → conflict (by recommended fingerprint)
7. Ensure behavior is stable with large ids (string-based)

---

## 9. Diagnostics and context usage

Optional:
- On DUP_CONFLICT, append a short diagnostic to `ctx.errors`:
    - `"ID_CONFLICT id=... canonical_line=... line=..."`
- Update counters:
    - `ctx.metrics["idem.canonical"] += 1`
    - `ctx.metrics["idem.replay"] += 1`
    - `ctx.metrics["idem.conflict"] += 1`

Ctx is not required for correctness.

---

## 10. Test plan (before code)

### Unit tests (pytest)

- `test_idempotency_first_is_canonical()`
- `test_idempotency_replay_detected()`
- `test_idempotency_conflict_detected_amount_diff()`
- `test_idempotency_conflict_detected_customer_diff()`
- `test_idempotency_multiple_duplicates_mixed()`
- `test_idempotency_fingerprint_independent_of_time_keys()`

Each test asserts:
- output cardinality = 1 per input
- correct `idem_status`
- correct `canonical_line_no`
- fingerprint equality where expected

---

## 11. Non-goals

- This step does NOT decide acceptance/decline.  
    Decision policy for duplicates is handled in `EvaluatePolicies` (Step 05).
- This step does NOT read/write window state (daily/weekly sums, attempts).
- This step does NOT perform prime checks or multipliers.
