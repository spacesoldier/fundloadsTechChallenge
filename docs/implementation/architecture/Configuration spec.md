# Configuration Spec

This document defines the configuration model for the solution.

Key rule:

> **Configuration defines composition and parameters. Code defines meaning.**  
> Configuration MUST NOT contain business logic (no expressions, no embedded code, no conditional DSL).

We use configuration to:
- select which steps run (enable/disable optional steps),
- select the policy pack (baseline vs experimental),
- set numeric parameters (limits, caps, multipliers),
- define time-window semantics (day/week keying).

The same executable + the same flow composition can produce different outputs by switching configs.

---

## 1. Config files and conventions

### 1.1 Files
- `config/baseline.yml`
- `config/exp_mp.yml`

### 1.2 General conventions
- YAML is preferred for readability.
- Unknown keys are rejected (fail fast).
- All numeric currency amounts are expressed in **minor units** (cents) OR in decimals (choose one and be consistent).
  - Recommended: store money internally as integer cents; allow config to use decimals for readability.

### 1.3 Validation approach
Configuration is validated at startup:
- schema validation (keys, types),
- semantic validation (e.g. limits are positive, multipliers ≥ 1),
- referential validation (step names exist in registry, policy names exist in rule registry).

If config is invalid, the program fails immediately with a clear error.

---

## 2. Top-level structure

```yaml
version: 1

pipeline:
  steps:
    - name: parse_load_attempt
    - name: compute_time_keys
    - name: idempotency_gate
    - name: compute_features
    - name: evaluate_policies
    - name: update_windows
    - name: format_output
    - name: write_output

scenario:
  name: baseline            # baseline | exp_mp
  description: "Baseline velocity rules"

domain:
  money:
    rounding: "half_up"     # fixed rounding rule if decimals ever appear
    currency: "USD"         # challenge uses USD
  time:
    timezone: "UTC"
    day_key: "utc_date"     # fixed
    week_key:
      mode: "calendar"      # calendar | rolling
      week_start: "MON"     # if calendar
      days: 7               # if rolling

idempotency:
  mode: "canonical_first"   # canonical_first
  on_conflict: "decline"    # decline
  decision_for_noncanonical: "decline"  # always decline duplicates
  conflict_reason_code: "ID_CONFLICT"   # optional for diagnostics

features:
  enabled: true
  monday_multiplier:
    enabled: false          # baseline: false, exp_mp: true
    multiplier: 2.0
    apply_to: "amount"      # amount | limits (default: amount)
  prime_gate:
    enabled: false          # baseline: false, exp_mp: true
    global_per_day: 1
    amount_cap: 9999.00
    prime_range:
      mode: "dataset_minmax"  # dataset_minmax | fixed
      fixed_max: 100000        # used only if mode=fixed

policies:
  pack: "baseline"          # baseline | exp_mp
  evaluation_order:
    - daily_attempt_limit
    - prime_global_gate
    - daily_amount_limit
    - weekly_amount_limit
  limits:
    daily_amount: 5000.00
    weekly_amount: 20000.00
    daily_attempts: 3

windows:
  daily_attempts:
    enabled: true
  daily_accepted_amount:
    enabled: true
  weekly_accepted_amount:
    enabled: true
  prime_daily_global_gate:
    enabled: false          # baseline false, exp_mp true

output:
  file: "output.txt"
  json:
    pretty: false
    ensure_ascii: false
  ordering:
    preserve_input_order: true
```

---

## 3. Step registry and step configuration

### 3.1 Step registry

The runtime maintains a registry mapping step names to step factories:

- `parse_load_attempt`
- `compute_time_keys`
- `idempotency_gate`
- `compute_features`
- `evaluate_policies`
- `update_windows`
- `format_output`
- `write_output`

Config references steps by name only. If a name is unknown — startup fails.

### 3.2 Per-step parameters

Step configuration is expressed as:

```yaml
pipeline:
  steps:
    - name: compute_features
      params:
        monday_multiplier: true
        prime_gate: true

```

Semantics:

- `params` are step-level knobs, but must map to explicit, documented parameters in the Step Spec.
- If a step receives unknown `params`, startup fails.

Recommended: keep most parameters under dedicated sections (`features`, `policies`, `windows`)  
and use `params` only for enabling/disabling sub-behavior inside a step.

---

## 4. Scenario selection

### 4.1 Why scenario exists

A scenario is a named configuration preset to produce a reference output.

Examples:

- `baseline`: velocity limits only
- `exp_mp`: baseline + Monday multiplier + Prime ID gate

### 4.2 Allowed scenario values

- `baseline`
- `exp_mp`

Scenario affects:
- which features are enabled,
- which windows/gates are enabled,
- which policy pack is selected.

Scenario must not change the flow shape beyond toggling optional steps that are already part of the flow.

---

## 5. Money semantics in configuration

### 5.1 Amount types

Config values for money are expressed as decimals (e.g. `5000.00`).

Internal representation should be:
- integer cents OR
- fixed decimal with explicit rounding rule.

This must be documented in `docs/domain/Time & Money Semantics.md`.

### 5.2 Validation

- limits must be ≥ 0
- caps must be ≥ 0
- multipliers must be ≥ 1 (unless explicitly allowed otherwise)

---

## 6. Time semantics in configuration

### 6.1 Timezone

- The challenge uses timestamps with `Z`, so default timezone is UTC.

### 6.2 Day key
- `day_key = UTC date (YYYY-MM-DD)`

### 6.3 Week key

Config chooses one:
- `calendar` week, with a configured start day (default MON)
- `rolling` window of 7 days (less likely for this challenge)

Only one week definition is allowed per config.

---

## 7. Idempotency semantics in configuration

### 7.1 Canonical record choice
- `canonical_first`: the earliest occurrence in the input stream becomes canonical.

### 7.2 Conflict behavior

If the same `id` appears with different payload:
- `on_conflict: decline` means the later record(s) become non-canonical and are declined.

All non-canonical records:

- must not mutate window state,
- still produce output rows.

---

## 8. Feature toggles

### 8.1 Monday multiplier

If enabled, events that occur on Monday apply an increased “risk factor”.  
Default semantics:
- multiplier applies to **effective amount** (preferred; stable and explicit)

Alternative (not recommended but configurable):
- apply multiplier to limits instead of amount (rarely used; keep as a toggle only if needed).

### 8.2 Prime gate

If enabled:
- only `global_per_day` prime-ID approvals are allowed per UTC day across all customers
- prime-ID attempts have a max amount cap

Prime checking may use:
- dataset min/max for the computed prime set, or
- fixed maximum bound

---

## 9. Policy pack configuration

### 9.1 Packs

- `baseline`: daily attempts + daily amount + weekly amount
- `exp_mp`: baseline + prime gate + monday multiplier effects

### 9.2 Evaluation order

Policy evaluation order is explicit and stable.  
Default order:
1. daily attempts
2. prime gate (if enabled)
3. daily amount
4. weekly amount

A declined decision stops further checks (first-failure semantics).  
Reasons are recorded for tests/diagnostics but not required in the challenge output.

---

## 10. Window configuration

Windows are enabled/disabled explicitly.  
If a policy depends on a disabled window, config is invalid.

---

## 11. Output configuration

- output file name is configurable (`output.txt` by default)
- output must preserve input order
- JSON formatting should match challenge expectations

---

## 12. Misconfiguration behavior (fail fast)

The program MUST fail at startup if:
- unknown keys exist
- unknown step names exist
- unknown policy names exist
- invalid types are provided
- semantic constraints are violated (negative limits, missing required params)

This prevents “silent wrong outputs”.
