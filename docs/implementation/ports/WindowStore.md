#  Intro

This document defines the **hexagonal boundary** for windowed state used by the decision engine.

Window state represents **velocity controls**:
- per-customer daily attempt counters,
- per-customer daily accepted amount sums,
- per-customer weekly accepted amount sums,
- optional global per-day prime-gate usage.

The core rule:
- **policy evaluation reads snapshots**
- **window updates are applied after the decision**
- the core never depends on a concrete storage technology.

---

**Service contract:** [window_store.py](../../../src/fund_load/services/window_store.py)  
**Service implementation (in-memory):** [window_store.py](../../../src/fund_load/state/window_store.py)

---

## 1. Purpose and scope

The WindowStore boundary provides two capabilities:

1) **Read snapshots** required to evaluate policies for a single event.
2) **Apply mutations** (increments / additions) in strict stream order.

It is intentionally minimal:
- no ad-hoc querying,
- no cross-customer reporting,
- no storage-specific features.

---

## 2. Conceptual model

### 2.1 Window keys

All keys are derived from the event timestamp normalized to UTC.

- `day_key_utc`: `YYYY-MM-DD`
- `week_key_utc`: `YYYY-MM-DD` of the week start (Monday)

### 2.2 Window types (state buckets)

Per customer:
- `DailyAttempts(customer_id, day_key_utc) -> attempts:int`
- `DailyAcceptedAmount(customer_id, day_key_utc) -> amount_sum:Money`
- `WeeklyAcceptedAmount(customer_id, week_key_utc) -> amount_sum:Money`

Global (optional):
- `DailyPrimeGate(day_key_utc) -> prime_approved_count:int`

### 2.3 Canonical-only updates

Only **canonical** events are allowed to mutate window state.
Duplicate replay/conflict attempts must not influence later decisions.

---

## 3. Port interfaces (core contracts)

The core uses two ports:

- `WindowReadPort`
- `WindowWritePort`

They may be implemented by a single adapter (e.g. a database-backed store), but the separation keeps responsibilities explicit.

---

## 4. WindowReadPort

### 4.1 Snapshot request

A read is always scoped to a single event’s keys:

- `customer_id`
- `day_key_utc`
- `week_key_utc`

### 4.2 Snapshot response

The response contains numeric values. Missing entries are treated as zeros by the core.

Fields (baseline):
- `day_attempts_before: int`
- `day_accepted_amount_before: Money`
- `week_accepted_amount_before: Money`

Fields (experimental pack only):
- `prime_approved_count_before: int`

### 4.3 Semantics

- Read operations are **side-effect free**.
- Reads must reflect all updates applied for earlier events in the same stream order.

---

## 5. WindowWritePort

### 5.1 Mutation types

All mutations are monotonic increments:

- increment daily attempts by `+1`
- add to daily accepted amount by `+Money`
- add to weekly accepted amount by `+Money`
- increment global daily prime usage by `+1`

### 5.2 Mutation conditions

- Daily attempts: apply for **every canonical attempt**, regardless of approval.
- Accepted sums: apply for **canonical + approved** only.
- Prime usage: apply for **canonical + approved + prime** only.

### 5.3 Ordering

Mutations must be applied in the same order as the input stream is processed.

---

## 6. Consistency expectations

For this challenge, we assume a single-threaded pipeline per stream (line order), so consistency requirements are simple:

- **read-after-write ordering** must hold within the process:
  - after updating windows for line N, reads for line N+1 must observe that state.

Transactionality:
- The port does not require multi-window atomic transactions, but implementations may use them.
- The minimal requirement is deterministic ordering.

---

## 7. Error handling

- If the store fails to read or write window state, the run should fail fast.
- Silent partial processing is not acceptable for the challenge.

---

## 8. Implementation notes (non-binding)

Possible adapters:
- in-memory store (tests)
- PostgreSQL adapter (analysis / reference output generation)
- embedded store (future)

The port itself must not mention storage details.

---

## 9. Testability

A fake in-memory implementation should exist for unit tests:
- deterministic initial state
- ability to inspect final state

Recommended patterns:
- `InMemoryWindowStore` implements both read/write ports.
- tests assert both decisions and state transitions.

---

## 10. Non-goals

- No historical queries (“give me all windows”).
- No time-range scans.
- No reporting / analytics.
- No eviction / TTL (not needed for the challenge dataset size).
- persistence
- concurrent writers

---

## 11. In-memory implementation for the challenge

For the take-home task we intentionally use an **in-memory window store** not only in tests, but also in the main run, because:

- The input is processed as a single ordered stream (file lines).
- The expected output is deterministic and does not require persistence across restarts.
- The window state size is small (bounded by customers × days/weeks observed in the file).
- It eliminates storage complexity and lets the evaluation focus on correctness and clarity.

**Important:** The code remains “hexagonal” because the engine talks to ports.
A DB-backed implementation can be added later without changing core logic.


---

## 12. In-memory implementation (reference)

### 12.1 Data structures

We use plain dicts keyed by tuples. This gives O(1) expected access and is easy to inspect in tests.

Suggested internal shape:

- `daily_attempts: dict[(customer_id:int, day_key:str), int]`
- `daily_accepted_amount: dict[(customer_id:int, day_key:str), int_cents]`
- `weekly_accepted_amount: dict[(customer_id:int, week_key:str), int_cents]`
- `daily_prime_gate: dict[day_key:str, int]`

Notes:
- store money as **integer cents** internally
- use `dict.get(key, 0)` defaults (no “missing row” handling needed)

### 12.2 Read semantics

`read_snapshot(customer_id, day_key, week_key) -> WindowSnapshot`

- returns values for the given keys
- missing entries are treated as 0
- returned snapshot is immutable (a dataclass or NamedTuple)

### 12.3 Write semantics

All writes are monotonic increments:

- `inc_daily_attempts(customer_id, day_key, +1)`
- `add_daily_accepted_amount(customer_id, day_key, +effective_amount_cents)` (approved only)
- `add_weekly_accepted_amount(customer_id, week_key, +effective_amount_cents)` (approved only)
- `inc_daily_prime_gate(day_key, +1)` (approved + prime only)

### 12.4 Ordering and concurrency

For the challenge:
- pipeline is single-threaded, so writes happen in stream order automatically.

The in-memory store is not required to be thread-safe.
If we ever add parallelism, we’d enforce partitioning (e.g., by customer_id) or add locks,
but that is out of scope.

### 12.5 Reset / lifecycle

The in-memory store is created once per run.
No persistence, no reload.

---

## 13. Consistency expectations

**Strict read-after-write** is required within the run:

- after applying updates for line N,
- reads for line N+1 must observe updated state.

With single-threaded processing, the in-memory store naturally satisfies this.

---

## 14. Error handling

In-memory operations should not fail under normal conditions.
If they do (programming error), fail fast.

---

## 15. Testing strategy (detailed)

We test WindowStore at two levels:

Implementation tests: [tests/ports/test_window_store_port.py](../../../tests/ports/test_window_store_port.py), [tests/adapters/test_window_store.py](../../../tests/adapters/test_window_store.py), [tests/usecases/steps/test_update_windows.py](../../../tests/usecases/steps/test_update_windows.py)

### 15.1 Unit tests: store behavior in isolation

Goal: prove the store implements the port semantics exactly.

Tests (suggested):

1) `test_read_snapshot_defaults_to_zero()`
- no prior writes
- read snapshot → all counters/sums are 0

2) `test_inc_daily_attempts_accumulates()`
- apply 3 increments for same (customer, day)
- read snapshot attempts == 3

3) `test_add_daily_amount_accumulates_cents()`
- add 100, add 250
- read snapshot daily sum == 350

4) `test_weekly_and_daily_are_independent()`
- same customer/day but different week keys
- confirm correct bucket isolation

5) `test_prime_gate_counter_defaults_and_increments()`
- prime counter defaults to 0
- increments work by day_key

Assertions:
- snapshot values match expected
- internal dict sizes match expected (optional, but useful)

### 15.2 Step-level tests: UpdateWindows uses store correctly

Goal: prove Step 06 calls the write port correctly under different decisions.

Use:
- InMemoryWindowStore as the real port implementation
- feed in Decisions and check state after each

Tests (suggested):

1) `test_updatewindows_canonical_approved_updates_attempts_and_sums()`
Input:
- canonical, approved, amount=100
Expected:
- attempts +1
- daily sum +100
- weekly sum +100
- prime unchanged unless enabled + prime

2) `test_updatewindows_canonical_declined_updates_attempts_only()`
Input:
- canonical, declined, amount=100
Expected:
- attempts +1
- sums unchanged

3) `test_updatewindows_noncanonical_does_not_touch_state()`
Input:
- non-canonical (dup replay or conflict)
Expected:
- no changes at all

4) `test_updatewindows_prime_gate_updates_only_when_enabled_and_prime()`
Run two configurations:
- baseline: prime gate disabled → no updates
- experimental: enabled → approved+prime increments

Assertions:
- final snapshot matches expected
- optionally, assert exact dict keys created

### 15.3 Scenario tests: EvaluatePolicies + UpdateWindows together

Goal: prove the read-before / write-after sequence is correct.

Pattern:
- process small hand-crafted sequences of 4–6 events
- after each event, assert:
  - decision reason (if declined)
  - window state progression

Scenarios:
- hitting daily attempt limit (4th attempt declined)
- hitting daily amount limit (decline only when would exceed)
- weekly amount accumulation across days in same week
- prime global gate (only first prime-approved per day allowed)

This layer is where “RSpec-like” behavior tests live.

---

## 16. What we do NOT test here

- parsing of amounts/time (covered by Step 01/02 tests)
- correctness of prime detection algorithm (covered by feature tests)
- formatting/output I/O (Steps 07/08 tests)
