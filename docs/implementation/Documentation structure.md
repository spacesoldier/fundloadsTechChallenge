# Documentation Structure (Architecture Pack)

This repository treats documentation as a first-class artifact.
Architecture and implementation follow a **docs → tests → code** discipline.

This document fixes the **documentation structure** (“architecture pack”) and the intended reading order.

---

## 1. Reading order

1) **[Architecture Overview](Architecture%20overview.md)** — the big picture, one flow + config-driven behavior  
2) **[Configuration Spec](Configuration%20spec.md)** — what can be configured and how (composition vs meaning)  
3) **[Flow Spec](Flow%20spec.md)** — the left-to-right data path as a sequence of steps  
4) [**Domain**](./domain/Domain%20docs%20index.md) — Message Types, semantics (time/money), reason codes  
5) **[[Steps Index|Steps]]** — one spec per step, single responsibility, explicit contracts  
6) [**Ports**](./ports/Ports%20Index.md) — interfaces and semantics for state and I/O  
7) **[Analysis](../analysis/data/Reference%20output%20generation.md)** — input data workup, reference outputs, and fixtures (already completed)

---

## 2. Folder layout

```css
implementation/
  Documentation structure.md <-- you are here
  Development plan.md
  Toolchain and Dependencies.md
  architecture/
	Architecture Overview.md
	Flow Spec.md
	Configuration Spec.md
  domain/
    Message Types.md
    Reason Codes.md
    Time and Money Semantics.md
  kernel/
    Composition Root Spec.md
    Context Spec.md
    Kernel Overview.md
    Runner (Orchestrator) Spec.md
    Scenario Spec.md
    ScenarioBuilder Spec.md
    Step Registry Spec.md  
  steps/
    Steps Index.md
    01 ParseLoadAttempt.md
    02 ComputeTimeKeys.md
    03 IdempotencyGate.md
    04 ComputeFeatures.md
    05 EvaluatePolicies.md
    06 UpdateWindows.md
    07 FormatOutput.md
    08 WriteOutput.md
  ports/
    WindowStore.md
    PrimeChecker.md
    InputSource.md
    OutputSink.md
    Ports Index.md

```



Notes:
- Step documents are numbered to preserve the **left-to-right** story.
- Scenario differences (baseline vs exp_mp) are represented as **configuration**, not separate flows.

---

## 3. What belongs where

### Architecture Overview
- one flow, two configs
- message + context model
- step contract and composition approach
- state model (windows and gates)
- idempotency strategy
- determinism guarantees
- testing strategy (golden outputs)

### Configuration Spec
- YAML structure and semantics
- how config selects steps and policy packs
- parameter validation rules
- misconfiguration behavior (fail fast vs tolerant modes)

### Flow Spec
- left-to-right list of steps
- input/output message types at each boundary
- optional steps and config toggles
- ordering rules and determinism constraints

### Domain docs
- immutable message types
- time bucketing semantics (UTC, week definition)
- money normalization rules
- reason codes and their meanings

### Step specs
- single responsibility
- explicit input/output contract
- invariants (INV-*) and edge cases
- config dependencies
- test plan (must exist before code)

### Ports specs
- interfaces (Protocols)
- semantics (read-before-write, atomicity assumptions)
- fake implementations used in tests
- adapter notes (file-based, in-memory)

### Analysis docs
- input workup
- reference output generation
- diff fixtures and how they were produced

---

## 4. Docs → Tests → Code gate

No production code should be written for a step until:
- its step spec exists and is **Frozen**
- every invariant (INV-*) has at least one test
- listed edge cases are covered (or explicitly deferred)

No end-to-end runner should be implemented until:
- all steps included in the flow are frozen and unit-tested
- at least one golden output test exists for the baseline config

---
