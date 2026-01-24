

This document specifies **ScenarioBuilder**: the component responsible for constructing a runnable `Scenario` from configuration by instantiating steps through the `StepRegistry` and wiring dependencies (ports, config sections, feature flags).

**Implementation:** [scenario_builder.py](../../../src/fund_load/kernel/scenario_builder.py)

`ScenarioBuilder` runs in the **composition/build phase** (startup time), not during message processing.

---

## 1. Purpose

`ScenarioBuilder` turns:
- **Flow configuration** (ordered steps + per-step params)
- **Registry** (named step factories)
- **Wiring context** (ports + shared services)

into:
- a **Scenario** object that contains an ordered list of step callables, ready to be executed by `Runner`.

Runner does not use the registry and does not understand configuration.  
It only executes `Scenario.steps` left-to-right.

---

## 2. Responsibilities

ScenarioBuilder must:

1. **Load and validate** scenario configuration (structure, required fields).
2. **Resolve steps** by name via `StepRegistry`.
3. **Instantiate steps** using step factories (with step-local config).
4. **Inject dependencies** (ports and shared services) into steps in a controlled way.
5. **Freeze the scenario** into an immutable structure usable by Runner.
6. **Produce clear errors** for invalid configurations (fail-fast at startup).

ScenarioBuilder must not:
- execute any business logic,
- perform IO (except reading config, if you choose to place config loading here),
- modify runtime state or windows,
- inspect input data.

---

## 3. Inputs and Outputs

### 3.1 Inputs

**A) Flow config**  
A minimal conceptual shape (format is up to you):
- `scenario_id` (string)
- `steps`: list of step declarations in order  
    Each declaration:
    - `name`: registry key
    - `config`: dict (step-local settings)

**B) StepRegistry**  
Registry provides:
- `has(name) -> bool`
- `build(name, *, step_config, wiring) -> Step`

**C) Wiring context**  
A bundle of dependencies passed to step factories, typically:
- `ports`: `InputSource`, `OutputSink`, `WindowStore`, `PrimeChecker` (depending on step)
- `policy_config` / `feature_config` (shared config trees)
- optional: `clock`, `trace_sink`, `logger` (if needed)
- optional: `mode` (baseline vs experiment)

### 3.2 Output

**Scenario**  
Conceptually:
- `scenario_id`
- `steps`: ordered list of `StepSpec` or directly of callables
- optional: `metadata` (source config fingerprint, version, etc.)

Recommended: scenario is immutable after build.

---

## 4. Core types (conceptual)

You can model them as dataclasses.

### 4.1 `StepDecl` (from config)

- `name: str`
- `config: Mapping[str, Any]`

### 4.2 `StepSpec` (built)

- `name: str`
- `step: Step` (callable `(msg, ctx) -> Iterable[msg]`)
- optional: `config_fingerprint: str` (for debug)
- optional: `tags: dict[str, str]` (e.g., “category”: “io”)

### 4.3 `Scenario`

- `id: str`
- `steps: Sequence[StepSpec]`

---

## 5. Build algorithm

Given `flow_config`, `registry`, `wiring`:

1. Validate `flow_config.steps` exists and is non-empty.
2. For each `StepDecl` in order:
    - ensure `decl.name` is known by registry
    - validate `decl.config` is a mapping (default `{}`)
    - call `registry.build(decl.name, step_config=decl.config, wiring=wiring)`
    - receive a callable step
    - wrap into `StepSpec(name=decl.name, step=callable, ...)`
3. Assemble `Scenario(id=flow_config.scenario_id, steps=[...])`
4. Optionally run **scenario-level validation**, e.g.:
    - required steps present (`ParseLoadAttempt` must be first)
    - `WriteOutput` must be last (if you enforce)
    - no duplicated “singleton” steps unless explicitly allowed
5. Return Scenario.

---

## 6. Validation rules

### 6.1 Structural validation (must-have)

- `scenario_id` present and non-empty
- `steps` exists and is a list
- each step entry:
    - has `name: str`
    - has `config` (mapping) or default `{}`

### 6.2 Registry resolution

- unknown step name => error (fail-fast)
- duplicate step names allowed (e.g., two `Tap` steps) unless the builder enforces uniqueness per specific step categories

### 6.3 Wiring validation (optional but recommended)

ScenarioBuilder may validate that required dependencies exist before building:

Example checks:
- if a config includes `UpdateWindows`, wiring must contain `WindowStore`
- if config includes `PrimeChecker`, wiring must contain it

However, a simpler approach is:
- step factory validates its own dependency requirements and raises a specific error (recommended).

---

## 7. Error model

Prefer typed errors with clear messages:
- `InvalidScenarioConfigError`
- `UnknownStepError(step_name)`
- `StepBuildError(step_name, cause)`
- `ScenarioConstraintError(message)`

Error messages should include:
- scenario id (if known)
- step index
- step name
- what exactly is wrong

Goal: user can fix config without reading code.

---

## 8. Determinism

ScenarioBuilder must produce stable output given the same:
- config
- registry content
- wiring

No random ordering, no iteration over dict keys without sorting when order matters.

---

## 9. Testing spec

Tests should be **small and fail-fast**, no integration IO.

### 9.1 Happy path: builds scenario in order

**Given**

- registry contains `A`, `B`, `C` step factories returning distinct callables
- config steps: `[A, B, C]`

**Expect**
- scenario.steps length = 3
- step names preserved in order
- returned callables are exactly those built by registry factories

### 9.2 Unknown step name

**Given**

- config includes step `X`
- registry does not contain `X`

**Expect**
- raises `UnknownStepError`
- message includes step index and name

### 9.3 Missing steps list

**Given**
- config has no `steps` or empty list

**Expect**
- `InvalidScenarioConfigError`

### 9.4 Step config must be mapping

**Given**
- config step has `config: "not a dict"`

**Expect**
- `InvalidScenarioConfigError` pointing to that step

### 9.5 Step factory error is wrapped

**Given**
- registry factory for step `B` raises `ValueError("bad")`

**Expect**
- `StepBuildError(step_name="B")`
- includes original exception info

### 9.6 Scenario constraint: Parse must be first (if enforced)

**Given**
- config steps: `[ComputeTimeKeys, ParseLoadAttempt, ...]`

**Expect**
- `ScenarioConstraintError` describing ordering requirement

(Only if you decide to enforce constraints at builder level.)

### 9.7 Dependency requirement surfaced clearly

**Given**
- step `UpdateWindows` requires `WindowStore`
- wiring does not contain it

**Expect**
- either:
    - `StepBuildError("UpdateWindows", MissingDependencyError("WindowStore"))`
    - or builder-level `InvalidWiringError`
- message points to missing dependency

### 9.8 Idempotent build: same inputs => same scenario fingerprint (optional)

If you compute a scenario fingerprint from config:
- build twice
- fingerprints match

---

## 10. Placement in project structure

Recommended location:

- `src/kernel/scenario_builder.py` (or `src/kernel/build.py`)
- `StepRegistry` in `src/kernel/step_registry.py`
- `Scenario` type in `src/kernel/scenario.py`

Builder is kernel-level because it is runtime-agnostic and contains no domain logic.
The service composition root (`src/service/main.py`) calls:
1. load config
2. construct wiring (ports + adapters)
3. `ScenarioBuilder.build(...)`
4. `Runner.run(...)`

---

## 11. Notes on “contracts vs ports” coupling

ScenarioBuilder does not decide whether steps call ports directly or via contracts.  
It just wires steps with the dependencies they require.

If you later refactor so steps talk to “contracts interfaces” instead of ports, ScenarioBuilder remains unchanged: only the wiring bundle and step factories change.

---

End of ScenarioBuilder spec.
