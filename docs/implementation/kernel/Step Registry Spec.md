

The **Step Registry** is a kernel-adjacent facility used by the **service composition root** to build a `Scenario` from configuration.

It maps a _stable step key_ (string) to:

- the step implementation (callable),
- its config schema/validator,
- optional metadata (contracts, tags, version).

**Important:** the runner never uses the registry. Only the scenario builder does.

---

## 1. Responsibilities

The Step Registry:

1. Provides **name → implementation** resolution (config-driven composition).
2. Validates step configuration using step-provided rules.
3. Exposes step metadata for documentation and tracing consistency.
4. Enables strict mode: fail fast on unknown steps or invalid config.
    

The Step Registry does not:

- execute steps,
- manage step order,
- contain business logic (it only wires and validates).

---

## 2. Structure

### 2.1 Registry key

A registry key must be:

- stable (does not change across refactors),
- descriptive,
- unique.

Recommended format:

- `parse.load_attempt`
- `time.compute_keys`
- `gate.idempotency`
- `features.compute`
- `policies.evaluate`
- `windows.update`
- `output.format`
- `output.write`

### 2.2 Registry entry

A single entry contains:

- `key: str`
- `factory: (cfg, ports) -> StepCallable`
- `config_spec: ConfigSpec` (shape + defaults)
- `validate: (cfg) -> list[str]` or raise `ConfigError`
- `contracts: {input_type, output_type}` (optional, but useful)
- `doc_ref: str` (link to step spec doc)

**Why factory instead of storing the step instance?**  
Because steps may require:

- step-local config binding (immutable),
- port instances (e.g., PrimeChecker, WindowStore, OutputSink),
- toggles (e.g., enable tracing hooks).

---

## 3. Composition rules

### 3.1 Pure steps vs portful steps

Registry supports both:

- **Pure**: no ports needed  
    Example: `ComputeTimeKeys`, `ComputeFeatures`, `FormatOutput`
- **Portful**: needs port references  
    Example: `IdempotencyGate` (may use a store), `UpdateWindows`, `WriteOutput`

The registry factory receives a `ports` bundle (already constructed by the service).

### 3.2 No hidden coupling

A registry entry must declare what it needs via its factory signature.  
No global singletons. No implicit imports of adapters.

---

## 4. Configuration validation

### 4.1 Strict vs permissive

For this challenge, prefer **strict**:

- unknown keys are rejected,
- missing required keys are rejected.

### 4.2 Defaulting

Defaults are allowed but must be explicit in `config_spec`.

Example:
- `week_mode: calendar_utc` default
- `idempotency.strategy: first_wins` default
- feature toggles: default disabled

---

## 5. Documentation integration

Registry entries should point to step specs.

This enables tooling:

- auto-generating “available steps” list,
- verifying that config references documented steps,
- consistent naming between docs and code.

---

## 6. Tests for Step Registry

### 6.1 Lookup

- registry resolves a known key.
- lookup of unknown key raises error.

### 6.2 Validation failures are precise

Given invalid config:
- error message includes:
    - step key
    - config path
    - expected vs actual

### 6.3 Factory binds config immutably

Given config A and config B:
- registry produces steps that behave differently
- no shared mutable config between them

### 6.4 Ports injection

Given a fake `WindowStore` / `PrimeChecker`:
- registry factory builds steps that use those fakes
- no adapter imports required

---

## 7. Where it lives in project structure

Recommended placement:
- `src/kernel/step_registry.py` (or similar)
- plus a `src/usecases/step_catalog.py` that registers the scenario-specific set

Reason:
- the mechanism is kernel-ish,
- the actual catalog of steps is usecase/service-specific.

---

## 8. Relationship to contracts layer

Step Registry should _not_ define domain semantics.  
If you introduce a `contracts/` layer:
- the registry can reference contract types for input/output (optional),
- but it should not invent new domain terms.

Registry is wiring + validation only.

---

End of Step Registry spec.