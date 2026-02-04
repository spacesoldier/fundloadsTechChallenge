
This document defines the **Composition Root**: the single place where the application is wired together.
It is responsible for constructing ports/adapters, building the scenario from configuration, and creating the runner.

The composition root is **not** a runtime component. It runs once at startup and produces a ready-to-run `AppRuntime`.

---

**Implementation:** [runtime.py](../../../src/stream_kernel/app/runtime.py)

## 1. Purpose

The composition root exists to enforce two rules:

1) **No framework magic**: wiring is explicit and readable.
2) **Dependency rule**: outer layers depend inward; wiring happens only at the boundary.

It prevents “hidden coupling” where domain/usecases start importing adapters, config loaders, or global singletons.

---

## 2. Responsibilities

The composition root must:

- Load and validate configuration (YAML/JSON/etc.)
- Instantiate **adapters** from config-declared factories
- Expose adapters to the application as **ports** (interfaces / contracts)
- Build a `Scenario` using:
  - `ScenarioBuilder`
  - `StepRegistry`
  - validated config sections
- Create the `Runner` with:
  - the built `Scenario`
  - `InputSource` / `OutputSink`
  - tracing options
  - `ContextFactory`
- Return a ready runtime object (or directly run it)

The composition root must not:

- Execute business logic (policies)
- Contain step logic
- Contain parsing/validation rules beyond configuration sanity checks
- Accumulate window state (that is delegated to ports)

---

## 3. Inputs and outputs

### 3.1 Inputs

- Validated config mapping (newgen structure)
- Environment overrides (optional, minimal)
- CLI arguments (input path, output path, config path, mode selection)

### 3.2 Outputs

Either:

A) `AppRuntime` object (recommended)
- `runner: Runner`
- `scenario: Scenario`
- `ports: PortsBundle` (optional exposure for diagnostics)
- `config: dict` (optional exposure for debugging)

or

B) A function that runs immediately (thin main)

---

## 4. Key components it wires

### 4.1 StepRegistry

A registry maps step names to factories:

- `name -> StepFactory(wiring, step_config) -> Step`

The registry is the **only** place where step implementations are discoverable.

### 4.2 ScenarioBuilder

Takes a flow spec from config and produces an ordered list of step instances, with stable naming and parameters.

### 4.3 Ports and adapters

Composition root constructs:

- Adapters (concrete implementations)
- Ports bundle (objects matching port contracts)

Example ports (from your docs):
- `WindowStore`
- `PrimeChecker`
- `InputSource`
- `OutputSink`

---

## 5. Dependency and import rule

The composition root is allowed to import everything.

Everything else must **not** import the composition root.

Practical enforcement:

- Place it in `src/service/` or `src/app/`
- Nothing in `kernel/`, `contracts/`, `domain/`, `usecases/`, `ports/` imports from `service/`

---

## 6. Lifecycle

### 6.1 Startup wiring flow

1. Read CLI args and env
2. Load config file
3. Validate config
4. Build adapters from config factories
5. Build ports/injection registry from adapter bindings
6. Create step registry (from discovery)
7. Build scenario (ScenarioBuilder)
8. Construct runner
9. Return runtime (or run it)

### 6.2 Runtime flow (outside composition root)

Runner:
- reads from `InputSource`
- executes scenario per message
- writes to `OutputSink`

---

## 7. Failure modes and policy

### 7.1 Config errors

Composition root must fail fast with clear messages:

- missing flow name
- unknown step name (not in registry)
- invalid step config schema
- invalid policy mode name

### 7.2 Adapter errors

Fail fast on startup if:
- input file missing
- output path not writable

(Unless you intentionally want lazy open; default: fail early.)

---

## 8. Tests for Composition Root

Composition root tests are *wiring tests*.
They ensure correct assembly and correct failure behavior.

### 8.1 Happy path builds runtime

**Given**
- minimal valid config
- input path exists
- output path writable

**Expect**
- `build_runtime(...)` returns `AppRuntime`
- runtime contains a `Runner`
- `Runner.scenario.steps` length matches config
- step names match config order

### 8.2 Unknown step name fails fast

**Given**
- config contains step `name: "NoSuchStep"`

**Expect**
- startup/build throws `UnknownStepError`
- error message contains the missing step name

### 8.3 Step config validation is enforced

**Given**
- step config missing required key

**Expect**
- `InvalidStepConfigError`
- error points to step index/name and missing key

### 8.4 Mode selection chooses correct flow config

**Given**
- config defines flows `baseline` and `experiment_mp`
- CLI selects `experiment_mp`

**Expect**
- scenario built from that flow
- step parameters reflect that mode (e.g. risk multiplier enabled)

### 8.5 Ports are correctly injected

**Given**
- use a fake WindowStore adapter

**Expect**
- steps that require WindowStore receive the same instance
- no step constructs its own store internally

### 8.6 Composition root does not leak adapters inward

Static-ish test (lint-level / conceptual):
- ensure no imports from `service/` inside `kernel/`, `domain/`, `usecases/`, `ports/`, `contracts/`

(Implement as a simple import-scan test later if you want.)

---

## 9. Placement in project structure

Recommended:

- `src/service/` (or `src/app/`)
  - `composition_root.py`
  - `config_loader.py`
  - `runtime.py` (optional AppRuntime wrapper)
  - `main.py` (CLI entrypoint)

This keeps “wiring” clearly separated from logic.

---

## 10. Summary

Composition root is the one-time startup wiring layer:

- Builds ports/adapters
- Builds scenario via registry + builder
- Builds runner
- Enforces dependency direction

It is the boundary where the “hexagonal” system is assembled into a runnable service.
