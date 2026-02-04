# Step Spec

**Step name (registry):** `compute_features`  
**File:** `docs/implementation/steps/04 ComputeFeatures.md`  
**Implementation:** [compute_features.py](../../../src/fund_load/usecases/steps/compute_features.py)  
**Responsibility:** Compute deterministic **derived features** required by policy evaluation, without performing any decisions or state access.

Features currently required by the challenge:
- Monday multiplier → `risk_factor` and `effective_amount`
- Prime-ID feature → `is_prime_id`

This step is pure (no external I/O). It may use precomputed in-memory lookup tables.

---

## 1. Contract

### Input
- `IdempotencyClassifiedAttempt`
  - `base: AttemptWithKeys`
  - `idem_status`
  - `fingerprint`, `canonical_line_no`

### Output
- `EnrichedAttempt`
  - `base: IdempotencyClassifiedAttempt`
  - `features: Features`

### Signature
`(msg: IdempotencyClassifiedAttempt, ctx: Ctx) -> Iterable[EnrichedAttempt]`

**Cardinality:** exactly 1 output per input.

---

## 2. Configuration dependencies

From `docs/implementation/architecture/Configuration spec.md` (newgen):

```yaml
nodes:
  compute_features:
    monday_multiplier:
      enabled: false            # baseline: false, exp_mp: true
      multiplier: 2.0
      apply_to: "amount"        # amount | limits (default: amount)
    prime_gate:
      enabled: false            # baseline: false, exp_mp: true
      global_per_day: 1
      amount_cap: 9999.00
```

Notes:
- `prime_gate.*` params are used by policies, but this step computes only `is_prime_id`.
- `apply_to` impacts how later policies interpret `effective_amount` vs limits.  
    For the challenge we recommend `apply_to: amount`.

---

## 3. Feature definitions

### 3.1 risk_factor

A multiplicative factor derived from temporal or other activators.

Baseline:
- `risk_factor = 1.0`

If Monday multiplier enabled:
- if event occurs on Monday (UTC) → `risk_factor = multiplier`
- else → `risk_factor = 1.0`

**Monday definition:**
- based on `attempt.ts` in UTC (not on local time).
    

### 3.2 effective_amount

A Money value derived from:
- raw amount (normalized Money from Parse step)
- risk_factor
- rounding rules (Money semantics)

Default semantics:
- `effective_amount = amount * risk_factor` (apply to amount)

Alternative semantics (config option):
- `effective_amount = amount` and policies apply multiplier to limits instead.  
    If this alternative is ever used, tests must verify equivalence and behavior.

### 3.3 is_prime_id

Boolean flag for whether `attempt.id` represents a prime number.

Rules:
- Only numeric ids are expected (validated earlier), but treat as string → parse to int.
- Prime detection strategy is implementation detail, but must be deterministic and efficient.

---

## 4. Prime detection strategy (recommended)

### 4.1 Range selection

We only need to detect primes within the dataset’s observed id range.

Recommended strategy:
- Precompute `min_id` and `max_id` while ingesting OR derive from config:
    - `prime_range.mode = dataset_minmax` (preferred)
    - `prime_range.mode = fixed` (fallback, e.g. up to 100000)

Because the pipeline is streaming, “dataset_minmax” requires a pre-pass if implemented purely.  
If we want single-pass streaming, use `fixed_max` or on-the-fly primality check with cache.

For the challenge, any deterministic method is acceptable.

### 4.2 Deterministic algorithm options

- **Cache + trial division** up to sqrt(n)
- **Sieve of Eratosthenes** up to `max_id` then O(1) membership checks

Recommended for simplicity and speed (small ranges like 4–5 digits):
- Sieve up to max_id (or fixed bound), store primes in a `set[int]`.

Memory:
- primes up to 100000 are small (well within MB-level limits).

---

## 5. Output construction

Given input `msg: IdempotencyClassifiedAttempt`:
1. Compute `risk_factor`
2. Compute `effective_amount`
3. Compute `is_prime_id` (only if prime feature enabled; otherwise `false`)
4. Construct `Features(risk_factor, effective_amount, is_prime_id, ...)`
5. Emit `EnrichedAttempt(base=msg, features=features)`

No field of the original message is mutated.

---

## 6. Invariants (INV-*)

### INV-01: Exactly one output per input

Always emits one `EnrichedAttempt`.

### INV-02: risk_factor is deterministic

Given the same UTC timestamp and config, risk_factor is identical.

### INV-03: effective_amount matches configured semantics

- If apply_to=amount: effective_amount == amount * risk_factor (with Money rounding)
- If apply_to=limits: effective_amount == amount

### INV-04: is_prime_id is deterministic

Given the same id and same prime strategy parameters, classification is stable.

### INV-05: Feature computation does not depend on idempotency status

`idem_status` does not influence computed features (classification affects policies later, not features).

---

## 7. Edge cases to cover (tests)

1. Monday vs non-Monday timestamps:
    - Monday in UTC must yield multiplier
2. Prime id vs composite id:
    - small known primes/composites
3. Large id near configured bound
4. Ensure Money multiplication rounding is stable (if decimals are used)
5. Prime feature disabled → is_prime_id is always false
6. Monday multiplier disabled → risk_factor=1 and effective_amount=amount

---

## 8. Diagnostics and context usage

Optional:

- `ctx.metrics["features.monday_applied"] += 1` when risk_factor != 1
- `ctx.metrics["features.prime"] += 1` when is_prime_id == true

Not required for correctness.

---

## 9. Test plan (before code)

### Unit tests (pytest)

- `test_features_baseline_no_multiplier_no_prime()`
- `test_features_monday_multiplier_applied()`
- `test_features_non_monday_multiplier_not_applied()`
- `test_features_prime_detection_enabled()`
- `test_features_prime_detection_disabled()`
- `test_features_effective_amount_rounding()` (only if needed)

All tests assert:
- output cardinality = 1
- correct risk_factor, effective_amount, is_prime_id

---

## 10. Non-goals

- No accept/decline decisions here.
- No window reads or writes here.
- No global “one prime per day” enforcement here (policy step).
- No idempotency resolution here (already classified).
