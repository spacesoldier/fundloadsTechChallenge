# Step Spec

**Step name (registry):** `compute_time_keys`  
**File:** `docs/implementation/steps/02 ComputeTimeKeys.md`  
**Responsibility:** Derive deterministic **UTC day/week keys** from a normalized `LoadAttempt`.

This step is pure: it performs no I/O and no state updates.

---

## 1. Contract

### Input
- `LoadAttempt`
  - `line_no: int`
  - `ts: datetime (UTC)`
  - other fields carried through

### Output
- `AttemptWithKeys`
  - `attempt: LoadAttempt`
  - `day_key: date` (UTC day)
  - `week_key: WeekKey` (derived per configuration)

### Signature
`(msg: LoadAttempt, ctx: Ctx) -> Iterable[AttemptWithKeys]`

**Cardinality:** exactly 1 output per input.

---

## 2. Configuration dependencies

From `docs/Configuration Spec.md`:

```yaml
domain:
  time:
    timezone: "UTC"
    day_key: "utc_date"
    week_key:
      mode: "calendar"     # calendar | rolling
      week_start: "MON"    # if calendar
      days: 7              # if rolling
```


### 2.1 Timezone

- For this challenge, `timezone` MUST be `UTC`.
- If a non-UTC timezone is configured, startup validation should fail (unless explicitly supported later).

### 2.2 Week key mode

Supported:
- `calendar` week (ISO-like, but configurable week start)
- `rolling` 7-day window (optional; if implemented, must be deterministic and well-defined)

Default for this challenge:
- `calendar` with `week_start = MON`

---

## 3. Key definitions

### 3.1 day_key (UTC date)

- `day_key = msg.ts.date()` where `ts` is already normalized to UTC.
- If `ts` is not UTC-aware (bug) → treat as invariant violation (should not happen if parsing step is correct).

### 3.2 week_key (calendar mode)

We define a `WeekKey` as:
- `week_start: weekday` (MON..SUN)
- `week_start_date: date` — the date of the week start for the given timestamp
- Optionally: `year` and `week_index` (for readability), but `week_start_date` is enough as a stable key.

Algorithm (calendar, week_start = W):
1. Let `d = day_key`
2. Compute `dow(d)` in range 0..6 (MON..SUN)
3. Compute `dow_start` for configured `week_start`
4. `delta = (dow(d) - dow_start) mod 7`
5. `week_start_date = d - delta days`
6. `week_key = CalendarWeekKey(week_start_date, week_start)`

This yields stable bucketing for any configured week start.

### 3.3 week_key (rolling mode) — optional

If enabled, a rolling window must be defined in a way that can be computed from the event alone.  
Recommended definition (if implemented later):
- `week_key = day_key` and window store sums over `[day_key-6 .. day_key]`  
    But this requires range queries in state, and complicates the pure “keying” idea.

For this challenge, rolling mode is expected to be **disabled**.

---

## 4. Output construction

Construct `AttemptWithKeys`:
- `attempt = msg`
- `day_key = utc_date(msg.ts)`
- `week_key = derived per config`

No other fields are modified.

---

## 5. Invariants (INV-*)

Each invariant must have unit tests.

### INV-01: Exactly one output per input

Always emits exactly one `AttemptWithKeys`.

### INV-02: day_key depends only on UTC timestamp

For the same UTC timestamp, `day_key` is identical regardless of machine locale.

### INV-03: week_key is deterministic and config-driven

For the same timestamp and same config, week_key is identical.

### INV-04: week bucketing is stable across the entire stream

Two events with the same computed `week_key` must belong to the same calendar week window per config.

---

## 6. Edge cases to cover (tests)

1. Timestamp at `00:00:00Z` and `23:59:59Z` same day → same `day_key`
2. Timestamp crossing day boundary → different `day_key`
3. Week boundary tests (calendar mode):
    - event on Monday belongs to week starting that Monday
    - event on Sunday belongs to week starting previous Monday (if week_start=MON)
4. Custom week_start (if supported by config):
    - same date re-buckets correctly if week_start changes (separate config test)
5. Leap day / year boundary:
    - late Dec / early Jan still produces correct `week_start_date`

---

## 7. Diagnostics and context usage

- This step may write computed keys into `ctx.tags` for debugging (optional).
- `ctx` is not required for correctness.

---

## 8. Test plan (before code)

### Unit tests (pytest)

- `test_time_keys_day_key_basic()`
- `test_time_keys_week_key_monday_start()`
- `test_time_keys_week_boundary_sunday()`
- `test_time_keys_year_boundary()`
- `test_time_keys_custom_week_start()` (optional)

All tests assert:

- output cardinality = 1
- day_key and week_key equal expected values for given timestamps/config.

---

## 9. Non-goals

- No policy evaluation.
- No window state reads or writes.
- No idempotency.
- No feature computation.