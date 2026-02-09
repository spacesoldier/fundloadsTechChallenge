# Intro

This document defines how the system determines whether an `id` is a **prime number** for the purposes of the “Prime ID” policy.

The prime logic is treated as a **feature provider**:
- it computes a boolean `is_prime` for each canonical event,
- the policy layer may then apply prime-related restrictions (if enabled).

---

**Service contract:** [prime_checker.py](../../../src/fund_load/services/prime_checker.py)  
**Service implementation (sieve):** [prime_checker.py](../../../src/fund_load/adapters/services/prime_checker.py)

---

## 1. Scope and intent

The challenge introduces an artificial rule:

- **Prime ID rule:** only one load with prime `id` is allowed per UTC day (global), and prime loads have an amount cap.

Therefore we need a deterministic and fast way to answer:

> “Is this `id` a prime number?”

This is an internal computation; the output format does not expose `is_prime`.

---

## 2. Input and output

### Input
- `id_num: int` (parsed numeric ID)

Constraints (from dataset observation):
- IDs are numeric strings, typically 4–5 digits, but the implementation must accept any non-negative integer.

### Output
- `is_prime: bool`

---

## 3. Prime semantics

Definition:
- An integer `n` is prime if:
  - `n > 1`
  - it has no positive divisors other than 1 and itself

Special cases:
- `n <= 1` → not prime
- even numbers:
  - `n == 2` → prime
  - `n % 2 == 0` and `n != 2` → not prime

---

## 4. Implementation approaches

Two valid strategies exist:

### A) On-the-fly primality test
For each `id_num`:
- test divisibility up to `sqrt(n)` (skipping evens)

Pros:
- simple, no memory
- fast enough for small n and small input

Cons:
- repeated work across many events
- harder to describe as a “sanctions list” analogue

### B) Precomputed prime set (recommended)
Compute all primes within a range once and store them in a `set[int]`.

Membership check:
- `is_prime = (id_num in prime_set)` → O(1) expected

Pros:
- deterministic, fast under load
- mirrors a “sanctions list” model (a finite suspicious-ID list)
- easy to cache and to unit test

Cons:
- requires choosing a range

---

## 5. Choosing the prime range (recommended)

For this challenge we treat the prime set as a “policy artifact” derived from observed data:

1) Scan the input stream (or the parsed batch) to find:
   - `min_id_num`
   - `max_id_num`

2) Precompute primes for `[min_id_num, max_id_num]` inclusive.

This keeps the set minimal and avoids overengineering.

Fallback behavior:
- If streaming-only mode is required and you cannot pre-scan:
  - precompute up to a safe bound (e.g., 100_000 or 1_000_000), or
  - use cached on-the-fly testing for values outside current range.

For our reference implementation we accept a simple two-pass mode for the challenge:
- pass 1: parse → gather id min/max
- pass 2: run pipeline

(If the final solution must be one-pass, switch to cached on-the-fly testing.)

---

## 6. PrimeSet generation algorithm

Recommended method: **Sieve of Eratosthenes**.

### 6.1 What is the sieve?
The sieve finds all primes up to N by:

- assuming all numbers are prime,
- iteratively marking multiples of each found prime as composite.

Complexity:
- time: ~ O(N log log N)
- memory: O(N)

For N around 100k, this is trivial on modern machines.

### 6.2 Memory considerations
If we store primes as a `set[int]`:
- number of primes up to 100,000 is ~9,592
- up to 1,000,000 is ~78,498
This is still reasonable in memory for the challenge context.

---

## 7. Port / API design

Prime checking should be a small service object with an explicit contract.

Suggested interface:

- `PrimeChecker.is_prime(id_num: int) -> bool`

And optionally:
- `PrimeChecker.from_range(min_n: int, max_n: int) -> PrimeChecker`
- `PrimeChecker.from_input_stats(min_id: int, max_id: int) -> PrimeChecker`

It must be:
- deterministic,
- side-effect free once constructed,
- safe to call from Step 04 (ComputeFeatures).

---

## 8. Testing strategy

Implementation tests: [tests/ports/test_prime_checker_port.py](../../../tests/ports/test_prime_checker_port.py), [tests/adapters/test_prime_checker.py](../../../tests/adapters/test_prime_checker.py)

### 8.1 Unit tests for primality semantics
- `is_prime(0) == False`
- `is_prime(1) == False`
- `is_prime(2) == True`
- `is_prime(3) == True`
- `is_prime(4) == False`
- `is_prime(17) == True`
- `is_prime(18) == False`
-  `is_prime(29023) == True`
- `is_prime(29417) == False`
-  `is_prime(31808) == False`
-  `is_prime(30593) == True`
-  `is_prime(24407) == True`
-  `is_prime(25293 == False`
-  `is_prime(11617) == True`
-  `is_prime(7723) == True`

### 8.2 Set membership correctness
Generate a PrimeChecker for a small range, assert known primes are included:
- range 1..30 contains {2,3,5,7,11,13,17,19,23,29}

### 8.3 Range selection behavior
If using min/max from input:
- ensure checker covers boundary values
- ensure values outside range return False OR trigger fallback rule (define explicitly)

Recommended for challenge:
- enforce range coverage and treat outside-range as “not prime” only if impossible in input;
- otherwise prefer fallback to on-the-fly check.

---

## 9. Non-goals

- cryptographic prime generation
- probabilistic primality tests
- prime factorization
- distributed prime services

---
