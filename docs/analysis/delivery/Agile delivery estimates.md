# Agile Delivery Estimate (Roles, Story Points, and Delivery Shape)

This document estimates the effort of delivering a **stream-processing decision engine** with deterministic semantics, configurable scenarios, explicit contracts, and testable reference outputs.

It is written as an internal planning artifact for a real engineering organization (not as a one-off exercise).

---

## 0) What “Done” means

### Inputs
- A stream of input events (NDJSON / line-based records).
- Canonical ordering defined by input ordering.
- Multiple configuration profiles (e.g., baseline vs experiment) that produce different outputs over the **same pipeline**.

### Outputs
- Deterministic decision outputs (stable ordering, repeatable results).
- Strictly defined behavior for:
  - idempotency and duplicates/conflicts
  - time-window limits (daily/weekly)
  - attempt counting and acceptance sums
  - “prime gate” rules (experiment mode)
- Full test suite:
  - unit tests for each step (behavioral)
  - contract tests for ports/adapters
  - scenario tests with golden outputs
  - diff tests between baseline/experiment
- Observability:
  - per-event trace log (in-memory + file sink)
  - future extension points for OpenTelemetry integration

### Non-goals (for this estimate)
- Distributed processing at scale (Kafka cluster, DB-backed windows, horizontal partitioning).
- Multi-threaded execution models or exactly-once streaming guarantees across processes.

---

## 1) Roles (who does what)

In a small org one person may wear multiple hats; estimation is split by responsibilities:

- **BA/SA (Analyst)**  
  Requirements, edge cases, acceptance criteria, dataset/format semantics, “what counts as correct”.

- **System Architect**  
  Architecture boundaries, dependency rules, contract model, scenario composition, runtime constraints.

- **Developer**  
  Tests, implementation, tooling, CI, integration, error handling.

- **Platform/Framework Developer (optional, if building a reusable kernel)**  
  Generic orchestration runtime, scenario builder, registry, trace infrastructure.

---

## 2) Estimation method

### Story Points (SP)
- Points represent uncertainty + complexity + integration overhead.
- Fibonacci-ish sizing: 1,2,3,5,8,13.
- This document uses **role-based points** (each role has its own load).

### Interpreting SP in planning
Teams calibrate SP differently. A typical working heuristic:
- **3 SP** ≈ 0.5–1 day of focused work (incl. tests + review)
- **5 SP** ≈ 1–2 days
- **8 SP** ≈ 2–4 days

This document aims to show **relative size** and **where the work actually lives**.

---

## 3) Work breakdown structure (epics → stories)

### Epic A — Requirements & semantics (clarity before code)
Goal: define stable acceptance criteria and all “weird cases”.

| Story | Description | BA/SA | Architect | Dev |
|---|---|---:|---:|---:|
| A1 | Define acceptance criteria + invariants | 5 | 2 | 1 |
| A2 | Input format semantics & anomalies (money tokens, timestamps, duplicates) | 8 | 2 | 2 |
| A3 | Idempotency rules + conflict semantics | 5 | 3 | 2 |
| A4 | Output schema + golden output rules | 3 | 1 | 2 |

**Subtotal**: BA/SA **21**, Architect **8**, Dev **7**

---

### Epic B — Architecture & contracts (make it buildable, not “a script”)
Goal: a structure that survives change.

| Story | Description | BA/SA | Architect | Dev |
|---|---|---:|---:|---:|
| B1 | Architecture boundaries: domain/contracts/kernel/usecases/ports/adapters | 2 | 8 | 2 |
| B2 | Step-level specs (contracts, invariants, acceptance per step) | 3 | 5 | 3 |
| B3 | Port specs (interfaces, assumptions, test fixtures) | 2 | 5 | 2 |
| B4 | Kernel specs (context, runner, scenario builder, registry, tracing) | 1 | 8 | 3 |
| B5 | Dev tooling baseline (lint/type/test rules + CI gates) | 0 | 1 | 3 |

**Subtotal**: BA/SA **8**, Architect **27**, Dev **13**

---

### Epic C — Delivery implementation (kernel assumed available)
Goal: implement behavior end-to-end with comprehensive tests.

| Story | Description | BA/SA | Architect | Dev |
|---|---|---:|---:|---:|
| C1 | Parse input into canonical domain values (Pydantic validators) | 1 | 1 | 5 |
| C2 | Time key computation (UTC/day/week semantics) | 1 | 2 | 3 |
| C3 | Idempotency gate behavior (first-wins + conflict capture) | 1 | 2 | 5 |
| C4 | Feature computation (config-driven toggles) | 0 | 1 | 3 |
| C5 | Policy evaluation (baseline vs experiment rules) | 1 | 2 | 5 |
| C6 | Window update semantics (attempts always, sums only on approved) | 1 | 2 | 5 |
| C7 | Output formatting + output writing | 0 | 1 | 3 |
| C8 | Scenario golden tests (baseline + experiment) | 0 | 1 | 5 |
| C9 | Trace sink (JSONL file output for grep/debug) | 0 | 1 | 3 |

**Subtotal**: BA/SA **5**, Architect **13**, Dev **37**

---

### Epic D — Integration & production hygiene (still “prototype”, but real)
Goal: stability, predictability, reviewability.

| Story | Description | BA/SA | Architect | Dev |
|---|---|---:|---:|---:|
| D1 | CLI ergonomics (paths, config selection, exit codes) | 0 | 1 | 3 |
| D2 | Error policy (fail-closed vs crash; consistent behavior) | 1 | 2 | 3 |
| D3 | CI wiring + quality gates (lint/type/test coverage) | 0 | 1 | 3 |
| D4 | Performance sanity checks (streaming, avoid quadratic traps) | 0 | 2 | 2 |

**Subtotal**: BA/SA **1**, Architect **6**, Dev **11**

---

## 4) Totals (delivery, assuming kernel/framework is already available)

- **BA/SA**: 21 + 8 + 5 + 1 = **35 SP**
- **Architect**: 8 + 27 + 13 + 6 = **54 SP**
- **Dev**: 7 + 13 + 37 + 11 = **68 SP**

This is already a multi-role workload: requirements + architecture + implementation + tests + tooling.

---

## 5) Additional scope: building a reusable kernel/framework (if not pre-existing)

If the organization wants a reusable orchestration runtime rather than a one-off pipeline, add:

### Epic F — Kernel/framework build-out (generic runtime)
Goal: reusable orchestration, composition, and observability.

| Story | Description | Architect | Dev |
|---|---|---:|---:|
| F1 | Define execution contract (step protocol, fan-out/worklist) | 5 | 3 |
| F2 | Context model + trace tape + diff policy | 5 | 5 |
| F3 | Step registry + scenario builder + config validation | 8 | 8 |
| F4 | Runner implementation + kernel-level tests | 5 | 8 |
| F5 | Trace sinks: JSONL + adapter surface for OpenTelemetry exporters | 3 | 5 |
| F6 | Dependency rules + packaging structure + developer ergonomics | 3 | 3 |

**Subtotal (framework)**: Architect **29 SP**, Dev **32 SP**

---

## 6) Grand totals (delivery + reusable kernel/framework)

- **BA/SA**: **35 SP**
- **Architect**: 54 + 29 = **83 SP**
- **Dev**: 68 + 32 = **100 SP**

This is the shape of a **serious internal product**, not a “small script”.

---

## 7) How this maps to real delivery timelines

A typical quarterly framing isn’t “one thing for one role”. It’s usually:

- multiple roles in parallel
- review / iteration loops
- refactors after new edge cases emerge
- documentation/training for maintainers

A realistic quarterly target would be:

### Quarter-level objective (example)
**Deliver a deterministic decision pipeline with two configuration profiles, golden outputs, and trace logs, with a clean architecture enabling future infra integrations.**

A common path:
- **Month 1**: requirements hardening + architecture/contracts + baseline E2E
- **Month 2**: experiment mode + trace sink + hardening + CI + diff tests
- **Month 3**: stabilization + maintainability + optional OpenTelemetry integration

---

## 8) Lean vs “serious” implementation modes (why estimates diverge)

Teams can reduce scope by cutting:
- formal docs/specs
- explicit port boundaries
- tracing
- reusable kernel generality
- golden output methodology

But the cost is:
- lower confidence
- harder changes
- less reuse
- more production risk

This estimate assumes **engineering-grade** deliverable: stable behavior, explicit contracts, and comprehensive tests.

---

## 9) Suggested backlog shape (Agile organization view)

### Sprint 0 (Discovery + architecture alignment)
- Epic A + core of Epic B
- Outcome: approved semantics, stable docs, golden reference outputs

### Sprint 1 (Baseline E2E)
- Core of Epic C + minimal Epic D

### Sprint 2 (Experiment + diff + trace sink)
- Remaining Epic C + tracing + diff tests

### Sprint 3 (Hardening + optional framework generalization)
- Epic D + optional Epic F items

