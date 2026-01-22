# Explanatory Design Note: Assumptions and Architectural Reasoning

## 1. Purpose of this document

This document captures the **interpretation choices, assumptions, and architectural rationale** behind the solution.
The codebase is intentionally kept implementation-focused, while this note explains *why* the system is structured as it is and how ambiguous parts of the challenge are resolved.

## 2. Problem framing

The challenge is treated as a reduced model of a **policy-driven adjudication engine**:
a stream of operation attempts is evaluated sequentially and each attempt is either **accepted** or **rejected** according to:
- velocity limits over time windows,
- calendar-based risk amplification (e.g. Mondays),
- identifier-based constraints (e.g. prime-number IDs),
- a mix of per-customer and global scopes.

The key technical property of the task is **stateful decisioning**:
each event is evaluated in isolation, but the decision depends on **historical aggregates** over time windows.

## 3. Core interpretation choices (explicit assumptions)

### 3.1 Time basis and timestamps
- All time calculations are performed in **UTC**, because the input timestamps are given in `...Z`.
- “Day” and “week” boundaries are defined explicitly (see window modes below) to avoid hidden timezone or locale assumptions.

### 3.2 Window definition: anchored vs rolling
Real systems use both, but the challenge statement is not explicit, so we make the mode **configurable**.

Two window modes are supported conceptually:

- **Anchored (calendar / aligned)**:
  - Day = a calendar day in UTC (00:00:00–23:59:59)
  - Week = a calendar week in UTC (configurable week start, default Monday)
- **Rolling (sliding)**:
  - Day = last 24 hours
  - Week = last 7 days

Default for this challenge: **anchored**, because “per day/per week” is typically interpreted as calendar-aligned unless explicitly stated otherwise.

### 3.3 “Monday counts as double”
The statement says: loads on Monday are “counted as double their value”.
We interpret this as an **amount transformation** for all amount-based checks.

We introduce:
- `raw_amount`: the original amount in the attempt
- `counted_amount`: the value used for amount-based policies

On Mondays (in UTC):
- `counted_amount = raw_amount * 2`
Otherwise:
- `counted_amount = raw_amount`

This is equivalent to “halving limits” for simple amount-limits, but is clearer and more composable once multiple policies exist.

### 3.4 Prime ID rule and interaction with Monday
The challenge introduces an extra constraint for prime-number IDs:
- only one prime-id load is allowed per day globally (across all clients)
- maximum amount for such a load is $9,999

Two ambiguous points exist:
1) Is the “maximum amount” checked against raw amount or counted amount?
2) Is the “per day” boundary anchored or rolling?

This solution treats prime checks consistently as amount-based controls:
- prime max amount is checked against the same amount basis used by amount-limits (default: **counted_amount**).
- prime “per day” uses the configured day window definition (default: anchored UTC day).

These choices are documented to remove ambiguity and keep the system internally consistent.

## 4. Architecture rationale

### 4.1 Pipeline model (separation of concerns)
Instead of embedding all logic into one procedural function, the solution is organized as a deterministic pipeline:

Input -> Input adapter -> Modifiers/Applicators -> Window aggregation -> Policy engine -> Output

This mirrors production decisioning systems where:
- the ingestion mechanism may change (files today, stream tomorrow),
- risk signals and transformations evolve,
- policies are audited and versioned,
- time windows are first-class concepts.

### 4.2 Adapters: decoupling sources from processing
- Input is consumed via an **Input Adapter** interface.
- Configuration is consumed via a **Config Adapter** interface.

In this challenge:
- Input adapter: file-based line-by-line stream reader.
- Config adapter: YAML loader.

This keeps the core engine independent from I/O details and makes the “streaming” requirement explicit.

### 4.3 Modifiers / Applicators: “act on the vector, don’t decide”
A recurring theme is preventing accidental coupling:
- modifiers transform/enrich the input vector,
- they do not accept/reject,
- they do not manage counters or window state.

Examples:
- calendar-based multiplier affecting amount basis,
- identifier-property detection resulting in flags (e.g. `prime_id` marker).

This yields a clean contract:
- “prepare facts” vs “make decisions”.

### 4.4 Windows: state management separated from decision logic
Policies require time-based aggregates (daily/weekly amounts, daily attempts, global daily counts).
A dedicated **window aggregation layer**:
- maintains counters and sums by window,
- provides a consistent API to policies,
- updates state only after acceptance (unless the task explicitly requires otherwise).

Window semantics (anchored/rolling) are treated as configuration, not hardcoded behavior.

### 4.5 Policies: single responsibility and explainability
Each policy checks one constraint and either:
- passes, or
- rejects with a specific reason.

Policies are kept independent:
- no I/O responsibilities,
- no transformation responsibilities,
- only evaluation logic based on vector + window state.

This structure supports explainability (“why rejected?”) and simplifies tests.

## 5. Configuration philosophy

### 5.1 Why configuration exists
Hardcoding numeric thresholds and rule toggles makes the solution less realistic and less auditable.
Even within a challenge, externalizing policy parameters improves:
- clarity (“this is the rule set”),
- traceability (“what exactly was configured?”),
- adaptability (tuning without code change).

### 5.2 What is *not* allowed in config (to avoid a DSL)
Configuration is intentionally **not executable**:
- no expressions
- no `eval`
- no arbitrary field references
- no user-defined code

Instead:
- only enumerated rule types,
- only numeric parameters and feature toggles,
- strict validation of allowed fields.

This avoids turning the project into a generic rules platform and keeps the solution safe and deterministic.

### 5.3 Control block and validation
A control block is responsible for:
- loading config,
- validating schema + semantics,
- exposing immutable config to modifiers and policies.

If the config is invalid, the system fails fast with a clear error.

## 6. Prime detection strategy (performance & rationale)

Prime-number ID checks are treated as a synthetic stand-in for “sanctions/watchlist-like” screening.
Two strategies are considered:
- on-the-fly primality check with caching,
- precomputed lookup (sieve) bounded by input range.

Given typical ID sizes in the provided data, precomputation can be bounded to a practical maximum (e.g. max observed ID), offering O(1) membership checks during processing while keeping memory usage controlled.

The implementation choice is documented and justified in the code comments and/or config notes.

## 7. Testing strategy (design intent)

The preferred approach is:
1) translate the narrative constraints into **explicit test cases**,
2) only then implement policies and window logic,
3) validate edge cases around boundaries and ambiguous semantics.

Test scope emphasizes:
- day/week boundaries (anchored vs rolling),
- Monday multiplier interactions,
- prime-id global/day behavior,
- separation between raw and counted amounts.

## 8. Non-goals (explicitly out of scope)
To keep the solution aligned with the challenge, the following are not modeled:
- distributed state replication / consensus
- external payment rails or settlement
- probabilistic risk scoring
- persistence design beyond what is strictly required
- a generic, user-programmable rule language

## 9. Summary

This solution treats the assignment as a constrained version of a real-world decisioning system:
- streaming processing,
- explicit state management via windows,
- clean split between transformations and decisions,
- configuration-driven parameters with strict validation,
- documented assumptions for all ambiguous points.

The result aims to be correct, explainable, and review-friendly without drifting into a generic rule engine.


## 10. Next step:
Proceed with data analysis
-  [[Input data analysis - idempotency|first look at the dataset - duplicates, conflicts and idempotency]]
- [[Reference output generation|generating the reference output]]
  