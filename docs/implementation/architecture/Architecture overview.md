# Intro

This document describes the high-level architecture of the solution.
It is intentionally **flow-first**, Ruby-friendly, and driven by explicit contracts.

The core idea is simple:

> **One deterministic flow, configured by two different configs, produces two different reference outputs.**

The same codebase and the same left-to-right step composition are reused across scenarios.
Scenario differences are expressed via configuration:
- step toggles (optional feature steps)
- parameter values (limits, multipliers, caps)
- policy pack selection (baseline vs experimental ruleset)

Configuration does **not** contain business logic.
Code defines meaning; config selects composition and parameters.

[More details about the rationale and implementation structure](./Project%20Structure%20and%20Architectural%20Rationale.md).

---

## 1. Problem model (what we simulate)

We process an ordered stream of “fund load attempts” and decide whether each attempt is **accepted** or **declined**.
Decisions are driven by:
- velocity limits (daily amount, weekly amount, daily attempts),
- idempotency constraints (duplicate IDs, including conflicting duplicates),
- additional regulatory-style controls (enabled via configuration).

Output must:
- preserve input order,
- be deterministic and reproducible,
- match precomputed reference outputs for each config.

---

## 2. Architecture style: Hexagonal + flow-first composition

We use a small hexagonal layout:

- **Domain**: immutable messages (value objects), decision semantics, reason codes.
- **Application**: step composition, scenario selection via config, deterministic orchestration.
- **Ports**: interfaces for state and utilities (window store, time keying, prime checker, sinks/sources).
- **Adapters**: file input, file output, YAML loader.
- **Runtime**: explicit wiring and step registry.

The primary unit of understanding is the **left-to-right data path**:
we describe the system by “what path an event takes through the system”.

---

## 3. Message + Context model

### 3.1 Message (immutable)
A message represents a domain event or its derived form.
Messages are immutable (value objects).

Examples:
- `RawLine`
- `LoadAttempt`
- `AttemptWithTimeKeys`
- `IdempotencyClassifiedAttempt`
- `EnrichedAttempt`
- `Decision`
- `OutputRow`

### 3.2 Context (mutable, short-lived)
Context is execution metadata attached to processing of a single message:
- trace ids
- counters / metrics
- debug notes / errors
- feature flags (resolved from config)

Rule:
- **Domain messages are immutable**
- **Context is mutable and short-lived**

---

## 4. Step contract and composition

A step is the basic unit of composition.
It has a single contract:

`(msg, ctx) -> Iterable[output]`

It may:
- return nothing (drop — rarely used here because we must emit outputs per input)
- return one output (map)
- return many outputs (fan-out)

Each step must do **exactly one conceptual thing**.
If a step mixes concerns, it is split.

Branching logic must not become inline if/else soup:
- branch as separate steps, or
- delegate to a rule catalog evaluated by a step.

We avoid framework magic:
- no hidden callbacks
- no implicit lifecycle hooks
- no “logic in decorators”

Wiring is explicit and visible.

---

## 5. One flow, two configs

### 5.1 The flow (stable)
The flow is a stable sequence of steps:

1) Parse & normalize input  
2) Compute time keys (UTC day/week)  
3) Idempotency gate (canonical vs duplicates)  
4) Compute optional features (config-driven)  
5) Evaluate policies (policy pack selected by config)  
6) Update window state (deterministic state updates)  
7) Format output row  
8) Write output

This is documented in detail in **Flow Spec.md** and **Step specs**.

### 5.2 Configuration (variable)
Configuration selects:
- which optional steps are enabled (e.g. extra features),
- which policy pack to use (baseline vs exp_mp),
- parameters for limits and caps.

Baseline and experimental outputs are produced by:
- the same flow
- the same step contracts
- the same runtime
- different configuration values

---

## 6. State model: windows and gates

Velocity limits require state. We model state as explicit window stores:
- daily attempts per customer/day
- daily accepted amount per customer/day
- weekly accepted amount per customer/week
- optional global daily gates (e.g. prime-ID approvals)

State rules (documented and test-covered):
- daily attempt counter updates for every canonical attempt, regardless of acceptance
- accepted amount windows update only on approval
- global gates update only on approval (and only for relevant events)

State access is isolated behind a `WindowStore` port.

---

## 7. Idempotency model: duplicate IDs

The dataset includes repeated IDs, including conflicting duplicates.

We implement an **Idempotency Gate** step that:
- classifies events into: `CANONICAL`, `DUPLICATE_REPLAY`, `DUPLICATE_CONFLICT`
- allows only canonical events to affect window state
- ensures every input line still produces a decision and output row
- preserves input order strictly

This is crucial for determinism and for matching reference outputs.

---

## 8. Determinism guarantees

We guarantee reproducibility by:
- single ordered pass (input order is preserved)
- UTC-based time keys
- explicit policy evaluation order
- explicit state update semantics
- no nondeterministic dependencies

Given the same:
- input file
- configuration
- runtime version

the output must be identical.

---

## 9. Testing strategy (RSpec mindset)

Testing is behavior-driven, even if the tool is pytest.

- each step is unit-tested against invariants
- flows are validated via golden output tests
- replay determinism is verified
- diff fixtures (baseline vs experimental) may be used as additional test artifacts

Critically:
**docs → tests → code** is enforced by repository conventions.

---

## 10. Related documents

- [[Configuration spec|Configuration]]
- [[Flow spec|Flow]]
- [[Steps Index|Steps]]: `docs/implementation/steps/*.md`
- [Ports](../ports/Ports%20Index.md): `docs/implementation/ports/*.md`
- [Domain](../domain/Domain%20docs%20index.md): `docs/implementation/domain/*.md`
- [Analysis and reference outputs](../../analysis/data/Reference%20output%20generation.md)
