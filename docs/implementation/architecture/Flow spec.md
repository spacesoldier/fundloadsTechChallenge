# Intro

This document defines the **single left-to-right flow** used by the program.

Key idea:

> We run the **same flow** for all scenarios.  
> Different outcomes (baseline vs exp_mp) are produced by **configuration**:
> - toggling optional behaviors inside steps,
> - selecting a policy pack,
> - setting parameters (limits, multipliers, caps).

The flow is designed to be Ruby-friendly:
- explicit contracts,
- single-responsibility steps,
- composition over inline branching,
- minimal framework magic,
- determinism by construction.

---

## 1. Flow contract (top-level)

### Input
A sequence of newline-delimited JSON objects (one object per line).

Each input line represents a fund load attempt.

### Output
A JSON array written to `output.txt`, preserving **input order**.

Each output element:
- `id` (string, as provided)
- `customer_id` (string, as provided)
- `accepted` (boolean)

The flow MUST emit exactly one output row per input line (even for duplicates/conflicts).

---

## 2. Message types (flow-level)

At flow boundaries we use immutable message types. The exact dataclass definitions live in `docs/domain/Message Types.md`.

Flow-level types:

- `RawLine`
  - `line_no: int`
  - `raw_text: str`

- `LoadAttempt`
  - `line_no: int`
  - `id: str`
  - `customer_id: str`
  - `amount: Money` (normalized)
  - `ts: datetime` (UTC)
  - `raw: dict` (optional, for diagnostics)

- `AttemptWithKeys`
  - `attempt: LoadAttempt`
  - `day_key: date` (UTC day)
  - `week_key: <WeekKey>` (per config)

- `IdempotencyClassifiedAttempt`
  - `attempt_with_keys: AttemptWithKeys`
  - `idem_status: CANONICAL | DUP_REPLAY | DUP_CONFLICT`

- `EnrichedAttempt`
  - `base: IdempotencyClassifiedAttempt`
  - `features: Features` (risk_factor, is_prime, effective_amount, etc.)

- `Decision`
  - `line_no: int`
  - `id: str`
  - `customer_id: str`
  - `accepted: bool`
  - `reasons: list[str]` (optional diagnostics, not required for output)
  - `idem_status: ...` (for traceability)

- `OutputRow`
  - `id: str`
  - `customer_id: str`
  - `accepted: bool`

---

## 3. Processing Context (Ctx)

The flow uses a mutable short-lived `Ctx` object:
- `trace_id`
- `errors: list[str]`
- `metrics: dict[str, float|int]`
- `tags: dict[str, str]`

Ctx must never be required for correctness; it is for diagnostics and test visibility.

---

## 4. The step sequence (left-to-right)

The flow is a stable ordered sequence of steps:

1) **ReadLine**  
2) **ParseLoadAttempt**  
3) **ComputeTimeKeys**  
4) **IdempotencyGate**  
5) **ComputeFeatures**  
6) **EvaluatePolicies**  
7) **UpdateWindows**  
8) **FormatOutputRow**  
9) **WriteOutput**

Each step is specified in `docs/steps/<NN StepName>.md`.

---

## 5. Step-by-step contracts and responsibilities

### Step 1 — ReadLine (Source Adapter)
**Contract:** `(None, ctx) -> Iterable[RawLine]`  
**Responsibility:** produce ordered `RawLine` messages with monotonically increasing `line_no`.

Notes:
- This is an adapter concern (file-based in this challenge).
- If an empty file is provided, output is an empty JSON array.

---

### Step 2 — ParseLoadAttempt
**Contract:** `(RawLine, ctx) -> Iterable[LoadAttempt | Decision]`

**Responsibility:**
- parse JSON
- normalize fields (id/customer_id as strings, money normalization, timestamp parsing)
- validate minimal schema

**Failure policy:**
- If a line cannot be parsed or normalized, it still must produce an output row.
- Therefore, parse failures may emit a `Decision` directly (declined with reason), bypassing downstream steps.

This avoids “dropping” events and preserves 1:1 input-to-output mapping.

Scenario impact:
- none (always on)

---

### Step 3 — ComputeTimeKeys
**Contract:** `(LoadAttempt, ctx) -> Iterable[AttemptWithKeys]`  
**Responsibility:** compute UTC day and configurable week key.

Scenario impact:
- week key definition comes from config (`calendar` vs `rolling`, `week_start`)

---

### Step 4 — IdempotencyGate
**Contract:** `(AttemptWithKeys, ctx) -> Iterable[IdempotencyClassifiedAttempt]`  
**Responsibility:** classify attempts by idempotency status:
- CANONICAL (first seen id)
- DUP_REPLAY (repeat with identical payload fingerprint)
- DUP_CONFLICT (repeat with different fingerprint)

Scenario impact:
- none (always on; behavior configured by idempotency section)

Key rule:
- only CANONICAL messages may affect window state updates later.

---

### Step 5 — ComputeFeatures
**Contract:** `(IdempotencyClassifiedAttempt, ctx) -> Iterable[EnrichedAttempt]`  
**Responsibility:** derive features needed by policy evaluation:
- `risk_factor` (e.g. Monday multiplier)
- `is_prime` (prime-id check)
- `effective_amount` (amount adjusted by risk_factor; or per config)

Scenario impact:
- features toggled by config (`monday_multiplier.enabled`, `prime_gate.enabled`)

Important:
- Feature computation must not mutate the original attempt message; it produces a new enriched message.

---

### Step 6 — EvaluatePolicies
**Contract:** `(EnrichedAttempt, ctx) -> Iterable[Decision]`  
**Responsibility:** decide accepted/declined for this attempt based on:
- idempotency status (non-canonical are typically declined)
- window snapshots (daily attempts, daily accepted sum, weekly accepted sum)
- enabled policy pack and parameters
- optional global gate (prime-id daily global approvals)

Scenario impact:
- selected by config (`policies.pack`)
- evaluation order is explicit (`policies.evaluation_order`)

Decision output MUST carry enough metadata for UpdateWindows to apply correct state updates deterministically.

---

### Step 7 — UpdateWindows (Stateful)
**Contract:** `(Decision, ctx) -> Iterable[Decision]`  
**Responsibility:** update window state deterministically:
- attempt counter updates for CANONICAL attempts regardless of acceptance
- accepted amount windows update only if accepted
- optional global gates update only if accepted and relevant (e.g. prime-id)

This step returns the same `Decision` (or an enriched decision) to continue the flow.

Scenario impact:
- which windows are enabled depends on config
- window key definitions depend on config

---

### Step 8 — FormatOutputRow
**Contract:** `(Decision, ctx) -> Iterable[OutputRow]`  
**Responsibility:** strip internal fields and produce the exact output row structure.

Scenario impact:
- none

---

### Step 9 — WriteOutput (Sink Adapter)
**Contract:** `(OutputRow, ctx) -> Iterable[None]`  
**Responsibility:** write output JSON array to the configured file.

Must preserve input order:
- output rows are emitted and stored in `line_no` order.
- the writer either:
  - buffers rows until completion, then writes JSON array, or
  - streams JSON array carefully while preserving order.

Challenge-friendly approach:
- collect `OutputRow` in a list by order; write at end.

---

## 6. Ordering, determinism, and state semantics

### 6.1 Ordering
- The flow preserves the input order (`line_no`) end-to-end.
- Every input line yields exactly one output row.

### 6.2 Determinism
Given the same input and config:
- the output must be identical.

No nondeterministic dependencies are allowed to influence decisions.

### 6.3 State update rule (canonical vs non-canonical)
- Only CANONICAL attempts update windows.
- Non-canonical attempts still get decisions but do not mutate state.

---

## 7. Scenario mapping (baseline vs exp_mp)

This flow does not change. Only config differs.

### Baseline config
- `features.monday_multiplier.enabled = false`
- `features.prime_gate.enabled = false`
- `policies.pack = baseline`
- only base windows enabled

### exp_mp config
- `features.monday_multiplier.enabled = true` (multiplier=2.0)
- `features.prime_gate.enabled = true` (global_per_day=1, cap=9999)
- `policies.pack = exp_mp`
- prime global gate window enabled

---

## 8. Testing expectations

### Step-level tests
Each step has unit tests for its invariants, documented in its Step Spec.

### Flow-level golden tests
For each config we run the full flow and compare produced output against the precomputed reference:
- baseline output fixture
- exp_mp output fixture

Optional:
- diff artifact tests (baseline vs exp_mp decision changes)

---

## 9. Related documents

- [[Configuration spec|Config model]]
- [[Architecture overview]]: `docs/Architecture Overview.md`
- [Step specs](../steps/Steps%20Index.md): `docs/steps/*.md`
- [Ports](../ports/Ports%20Index.md): `docs/ports/*.md`
- [Domain](../domain/Domain%20docs%20index.md): `docs/domain/*.md`
- [Reference output generation](../../analysis/data/Reference%20output%20generation.md)
