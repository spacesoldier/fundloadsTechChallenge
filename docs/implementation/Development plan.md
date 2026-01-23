# Development Plan (Ruby-Friendly Python, Flow-First)

This document defines how we will implement the challenge solution in a way that feels natural to Ruby teams:
**flow-first**, **explicit contracts**, **composition over branching**, and **minimal framework magic**.

The primary unit of understanding is **the left-to-right data flow**, not “what happens inside a function”.

[The report written after the development completion](./Implementation%20report.md)

---

## 1. Principles (non-negotiable)

1) **The flow must read left-to-right**  
   We describe the system as “the path an event takes through the system”.

2) **Each step does exactly one thing (Single Responsibility)**  
   A step is a message transformation (or a strictly isolated side-effect).

3) **Clear boundaries and explicit contracts**  
   Every step has explicit input/output types (dataclasses / TypedDict / Protocol).

4) **No if–else soup in the middle of the flow**  
   Branching happens via:
   - separate steps, or
   - declarative rule catalogs invoked by a step  
   Control flow is composed, not hardcoded.

5) **Minimal framework magic**  
   Avoid hidden callbacks, decorators that trigger logic, implicit lifecycle hooks.
   Prefer explicit wiring and visible composition.

6) **Configuration defines composition, code defines meaning**  
   *Configuration selects:*
   - which steps run
   - in what order
   - with which parameters  
 
   *Code defines:*
   - what a step means
   - what it expects
   - what it guarantees  
   Configuration must never contain business logic.

7) **Docs → Tests → Code**  
   We do not write production code for a step until:
   - its spec is frozen,
   - its invariants are covered by tests.

---

## 2. High-level architecture (Hexagonal + DDD mindset)

We treat the program as a small hexagonal system:

- **Domain**: value objects, invariants, decision semantics, reason codes.
- **Application**: step composition, scenario selection, orchestration.
- **Ports**: interfaces for state (windows), clock/time keying, prime checking, sinks/sources.
- **Adapters**: file input, file output, YAML config loader.
- **Runtime**: explicit wiring, step registry, and deterministic execution.

The system is intentionally single-process and deterministic for this challenge.

---

## 3. The Domain Model: Message + Context

### 3.1 Message (immutable)
A Message is an immutable domain object representing the event itself.

Examples:
- `RawLine`
- `LoadAttempt`
- `CanonicalLoadAttempt`
- `EnrichedAttempt`
- `Decision`
- `OutputRow`

### 3.2 Context (mutable, short-lived)
Context is processing metadata that exists only during evaluation of one message:
- trace id
- diagnostics
- counters/metrics
- error list

Rule:
- **Messages are immutable**
- **Context is mutable and short-lived**

---

## 4. Step contract and composition model

A step is defined by a single contract:

`(msg, ctx) -> Iterable[output]`

It may:
- return nothing (drop — used sparingly; for this challenge we usually must emit an output row)
- return one value (map)
- return many values (fan-out)

This mirrors Ruby’s `map / select / flat_map` semantics.

### 4.1 Core combinators (small standard library)
We keep a minimal pipeline DSL:
- `Map` (transform)
- `Filter` (predicate pass-through)
- `Tap` (side-effects without modifying the message flow)
- `Pipeline` (composition)

No operator overloading. No decorators that hide behavior.

---

## 5. System flows (left-to-right)

We implement one or more **scenario flows**.

### 5.1 Baseline flow (core velocity rules)
`RawLine → Parse → TimeKeys → IdempotencyGate → Policy(baseline) → WindowsUpdate → OutputRow → OutputSink`

### 5.2 Experimental flows (optional)
Example:
- Mondays multiplier + Prime-ID global gate

Same overall shape, different policy/features steps:
`… → Features(exp_mp) → Policy(exp_mp) → …`

---

## 6. State model (windows and gates)

Velocity limits require state:
- daily attempts per customer/day
- daily accepted amount per customer/day
- weekly accepted amount per customer/week
- optional global daily gates (prime-id)

State is allowed but must be isolated:
- owned by stateful steps / stores
- accessed only through explicit port interfaces
- updated deterministically in stream order

Key rule:
- **attempt count updates apply regardless of acceptance**
- **amount windows update only on approval**
- **global prime gate updates only on approval of prime-id events**

---

## 7. Idempotency model (duplicate IDs)

Input data may contain repeated IDs, including conflicts (same ID with different payload).
We implement an **Idempotency Gate** step:
- classifies events into:
  - canonical
  - replay duplicate
  - conflict duplicate
- allows only the canonical event to affect window state
- ensures every input line still produces an output decision (preserving order)

---

## 8. Determinism guarantees

We guarantee reproducibility by:
- single ordered pass (`line_no` order)
- UTC-based time keys
- explicit order of policy checks
- explicit state updates (no hidden side-effects)
- no nondeterministic data structures influencing outcomes

---

## 9. Documentation structure (spec-first)

Docs are first-class and drive testing and implementation.

Proposed structure:

- `docs/flow/` — flow specs (scenario-level)
- `docs/steps/` — step specs (contract + invariants)
- `docs/ports/` — port specs (interfaces + semantics)
- `docs/domain/` — value objects, reason codes, key semantics
- `docs/analysis/` — already completed: data workup + reference outputs

---

## 10. The “Docs → Tests → Code” gate

### 10.1 Step-level gate
No code in `src/` for a step until:
- `docs/steps/<step>.md` is **Frozen**
- every `INV-*` has at least one unit test
- edge cases listed in the spec are covered or explicitly deferred

### 10.2 Flow-level gate
No end-to-end runner until:
- all included steps are frozen and tested
- at least one golden test exists that matches the reference output

---

## 11. Implementation order (work plan)

We implement from the edges inward, always enabling fast feedback:

1) Domain types (message dataclasses, reason codes)
2) Parsing & normalization step
3) Time keying step (day/week, monday tagging)
4) Idempotency gate step
5) Window store port + in-memory adapter (for challenge runtime)
6) Baseline policy step
7) Windows update step
8) Output formatting + sink
9) Golden baseline test (match `output.txt`)
10) Experimental scenario steps (features + policy + gate) + golden test (optional)

---

## 12. Deliverables

- Clean, readable repository with explicit flow composition
- Frozen specs for flows, steps, ports, and domain types
- Unit tests for invariants per step
- Golden end-to-end tests matching reference outputs
- Minimal dependencies, explicit wiring, no framework magic
