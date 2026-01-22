
This repository contains a technical challenge solution for a funds load adjudication engine
(velocity limits, idempotency handling, deterministic output).

## Primary goals
- Deterministic decisions for a given input file.
- Fully reproducible reference output generation.
- Strict preservation of input order in the produced output.
- A clean, test-driven Python implementation aligned with the documented domain model.

## Ground truth documents (do not silently reinterpret)
- `docs/Challenge task.md` — original task text.
- `docs/Solution.md` — high-level solution overview and architecture.
- `docs/analysis/data/Input data analysis - idempotency.md` — duplicate/conflict handling rules.
- `docs/analysis/data/Reference output generation.md` — how the reference output is generated.
- `docs/analysis/Reasoning and assumptions.md` — architectural reasoning and assumptions.

## Repository layout (current + intended)
- `Readme.md` — entry point and navigation.
- `AGENTS.md` — instructions for assistants/agents (this file).
- `docs/` — documentation is the primary source of truth for:
  - domain concepts and invariants
  - module boundaries and responsibilities
  - assumptions and ambiguous points from the task
  - reference data workup and reproducible artifacts
- `src/` — Python application code (to be implemented).
- `tests/` — Python tests (TDD-first, should drive implementation).

## Development workflow (docs → tests → code)
When implementing or modifying any module:

1. **Read the documentation first**
   - Identify the specific document(s) describing the module, its responsibilities, and invariants.
   - If module-level docs do not exist yet, create/extend them in `docs/` before writing code.

2. **Write tests before code**
   - Encode the module’s invariants and edge cases as tests in `tests/`.
   - Prefer small, targeted tests that collectively cover the full contract.

3. **Only then implement production code**
   - Write or adjust code under `src/` strictly to satisfy the tests.
   - Do not “wing it” with undocumented assumptions. If a new assumption is needed, document it first.

This repo treats documentation + tests as the specification. Production code must follow them.

## Rules for agents / assistants
1. Output contract is strict:
   - Do not change the required output schema or file name (`output.txt`) unless explicitly asked.
   - Do not reorder output records. Output order must match input order.
2. Do not "simplify away" documented behavior:
   - Idempotency gate behavior (canonical selection + replay vs conflict classification) is defined in docs.
   - Conflicting duplicate IDs must be preserved as separate input lines and handled explicitly.
3. Avoid introducing non-determinism:
   - No randomization in core logic.
   - Be explicit about timezone (UTC unless specified otherwise).
4. Keep dependencies minimal:
   - If a new dependency is proposed, justify it and keep it optional.
   - Prefer standard library where feasible.
5. Do not refactor or rewrite documents unless asked:
   - `docs/` is a curated set of reasoning and domain specs.
   - If something looks inconsistent, surface it as a note instead of “fixing” it silently.

## About multiple AGENTS.md files
This repo uses a single top-level `AGENTS.md` as the main instruction entry point.
If the repository later evolves into multiple independent modules/subprojects, local `AGENTS.md`
files may be added inside those subdirectories, but only if they add meaningful module-specific rules.
