

A **Scenario** is a fully constructed, executable flow: an ordered list of steps + the configuration that binds those steps together for a specific run (baseline vs experiment).

**Implementation:** [scenario.py](../../../src/fund_load/kernel/scenario.py)

The kernel executes _a scenario_. The service (composition root) _builds_ a scenario.

---

## 1. Responsibilities

Scenario is responsible for:

1. Defining the **step order** (left-to-right flow).
2. Providing each step with its **step-local config** (immutable at runtime).
3. Providing step identity for tracing (stable `step_id`, `step_name`).
4. Validating that the scenario is **well-formed** before execution.

Scenario does not:
- do any IO,
- store any state (windows, prime gates, etc.),
- interpret business rules (that’s inside the relevant step).

---

## 2. Structure

Conceptually, a scenario contains:
- `scenario_id` (e.g. `"baseline"` / `"exp_mp"`)
- `steps: list[BoundStep]`

Where `BoundStep` is:
- `step_id: str` (e.g. `"01.parse_load_attempt"`)
- `name: str` (human-readable)
- `step: StepCallable`
- `config: StepConfig` (typed structure or mapping)
- `enabled: bool` (optional)
- `tags: dict[str,str]` (optional: for tracing / filtering)

**Design rule:** scenario owns _composition_, steps own _meaning_.

---

## 3. Config binding

Scenario construction takes a **ScenarioConfig** (YAML) and uses a **StepRegistry** to resolve names into step implementations.

Example config shape (illustrative):

```yaml
scenario_id: baseline
steps:
  - id: 01.parse
    name: ParseLoadAttempt
    uses: ParseLoadAttempt
    with: {}
  - id: 02.time_keys
    name: ComputeTimeKeys
    uses: ComputeTimeKeys
    with:
      timezone: UTC
      week_mode: calendar_utc   # or rolling_7d
  - id: 03.idempotency
    uses: IdempotencyGate
    with:
      strategy: first_wins
      conflict_sink: record
  - id: 04.features
    uses: ComputeFeatures
    with:
      features:
        - prime_id
        - monday_multiplier
  - id: 05.policies
    uses: EvaluatePolicies
    with:
      limits:
        daily_amount: 5000
        weekly_amount: 20000
        daily_attempts: 3
  - id: 06.windows
    uses: UpdateWindows
    with:
      update_attempts_always: true
  - id: 07.format
    uses: FormatOutput
    with:
      output_mode: json_lines
  - id: 08.write
    uses: WriteOutput
    with: {}
```

Notes:
- `uses` is registry key.
- `with` is step-local configuration.
- `id` is stable identifier for tracing and diffing.

---

## 4. Validation rules

Scenario validation happens at build time (service composition root), before execution.

### 4.1 Registry existence

Every `uses` must exist in the StepRegistry.

### 4.2 Config schema sanity

Each step can expose a `validate_config(cfg) -> list[str]` or raise a `ConfigError`.

Rules:
- unknown keys: allowed or rejected (choose strict mode for this challenge)
- missing required keys: rejected
- incompatible combinations: rejected

### 4.3 Ordering constraints (optional but recommended)

Some steps require prior steps:
- `ComputeTimeKeys` must run after parsing (it needs a timestamp field)
- `EvaluatePolicies` must run after features/time keys exist
- `UpdateWindows` should run after policies are evaluated (commit logic)
- `FormatOutput` must run after decision is available

Scenario enforces these by either:
- explicit dependency declarations per step, or
- a simple hard-coded ordering check for this task (acceptable).

### 4.4 Uniqueness

- step `id` must be unique in the scenario.
- step `name` is optional but should be unique for readability.

---

## 5. Scenario entry semantics

Kernel runner treats the scenario as a list of `BoundStep`s. It does not know about parsing.

To keep kernel generic, scenario should define its entry contract:

- **Option A (simpler):** runner starts with raw `str` message; first step parses.
    
- **Option B (cleaner):** scenario has `entry(raw, ctx) -> Iterable[msg]`, typically parsing there.

For this challenge, choose **Option A**:
- keeps kernel minimal
- keeps all transformations inside steps (consistent with step specs)

---

## 6. Baseline vs experiment

Baseline and experiment differ only by **ScenarioConfig**.

Examples:
- In experiment, `ComputeFeatures` enables `monday_multiplier`
- In experiment, `EvaluatePolicies` includes `prime_id_global_gate` constraints
- Same step implementations, different configuration.

This is the key goal:
- identical pipeline shape
- differing feature/policy config
- deterministic diffs validated by reference outputs

---

## 7. Tests for Scenario

Scenario tests are build-time tests; they do not execute business logic.

### 7.1 Registry resolution

Given a registry with known steps:
- scenario config resolves all steps successfully.

### 7.2 Missing step fails fast

Config references an unknown `uses`:
- scenario builder raises ConfigError with clear message.

### 7.3 Ordering constraints

Given an invalid ordering:
- scenario builder rejects it (or warns, depending on chosen strictness).

### 7.4 Step config validation

Given an invalid config key/value:
- scenario builder rejects it with a precise error path (e.g. `steps[4].with.limits.daily_amount`).

### 7.5 Scenario identity

Scenario exposes:
- scenario_id
- list of step_ids in order  
    These are stable across runs for tracing and diffing.

---

## 8. Notes on future flexibility

Scenario is the kernel’s “unit of execution”. If later you need:
- conditional steps
- feature flags
- multiple subflows
- parallel branches

You can introduce these without changing steps:
- Scenario can expand into a static step list (compile-time branching),
- or kernel runner can support a small control graph.

For this take-home: keep it linear and explicit.

---

End of Scenario spec.
