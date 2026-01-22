# Toolchain & Dependencies

This document explains **which dependencies and development tools** are used in the project and **why**.
The goal is to keep the runtime minimal, while maintaining a strong engineering workflow (TDD, explicit contracts, reproducibility).

---

## 1. Principles

### 1.1 Minimal runtime footprint
Runtime dependencies must be justified by one of:
- correctness (validation, parsing, safety),
- explicit contracts (types, invariants),
- project requirements (config, IO format).

Everything else should be **dev-only**.

### 1.2 Explicit contracts over framework magic
The project favors:
- explicit schemas (models),
- deterministic parsing and normalization,
- test-first development,
- clear error reporting.

### 1.3 Reproducibility
The toolchain must support:
- deterministic formatting/linting,
- consistent type checks,
- repeatable test runs,
- pinned dependency versions (lock file).

---

## 2. Runtime dependencies

### 2.1 Pydantic
**Purpose**
- Parse and validate input records.
- Normalize “dirty” fields (money formats, timestamps).
- Provide explicit models for messages and outputs.

**Why it’s used**
- The input contains inconsistent money formats (e.g., `USD$123.00`).
- We need deterministic normalization rules and strong invariants.
- Validation failures should be structured and testable.

**Scope**
- Used at the boundary: parsing + canonicalization.
- Domain objects remain stable and explicit (can be Pydantic models or dataclasses wrapped by Pydantic parsing).

---

### 2.2 PyYAML
**Purpose**
- Read YAML configuration for scenario composition and policy parameters.

**Why it’s used**
- YAML matches the design principle: configuration selects composition; code defines meaning.
- Widely available and stable.

**Scope**
- Composition-time only: load + validate config.

---

### 2.3 Rich (optional runtime / preferred dev-only)
**Purpose**
- Better human-facing CLI output: progress, tables, errors.

**Why it’s used**
- Improves developer experience without affecting business logic.
- Can remain dev-only unless the final CLI output benefits from it.

**Scope**
- CLI/diagnostics only. Must not become a required dependency of core/domain logic.

---

## 3. Development-only dependencies

### 3.1 Pytest
**Purpose**
- Run unit tests and scenario tests.
- Support parametrization and fixtures.

**Why it’s used**
- Strong fit for TDD and “RSpec-like” behavior-first tests.
- Minimal friction for step-by-step specs.

---

### 3.2 pytest-cov
**Purpose**
- Coverage reporting.

**Why it’s used**
- Ensures step specs and error paths are actually covered.
- Helps avoid “untested happy-path only” implementation.

---

### 3.3 Ruff
**Purpose**
- Fast linting and formatting.

**Why it’s used**
- One tool replaces multiple Python linters/formatters in practice.
- Very fast feedback loop, useful when iterating on many small modules/steps.
- Keeps the codebase consistently readable.

---

### 3.4 MyPy
**Purpose**
- Static type checking.

**Why it’s used**
- Enforces explicit contracts between steps, ports, and message types.
- Catches mismatches early (especially in pipeline composition code).
- Matches the project’s “explicit contracts” philosophy.

---

## 4. Dependency classification

| Category | Examples | Allowed usage |
|---------|----------|---------------|
| Runtime | pydantic, pyyaml | Parsing/validation/config at boundaries |
| Dev-only | pytest, ruff, mypy | Testing, linting, type checks |
| Optional | rich | CLI UX / diagnostics (should not be required by core) |

---

## 5. Policy: keeping runtime clean

- Core/domain/kernel code must not import dev-only tooling.
- If Rich is used, keep it confined to CLI entrypoints.
- Pydantic should be used for **boundary validation** and canonicalization.
- Avoid introducing heavy frameworks unless required by the challenge.

---

## 6. Notes on Pydantic vs persistence

Pydantic is **not** a database library.
It does not replace:
- ORMs (SQLAlchemy),
- migrations,
- persistence schemas.

What it *does* provide:
- validated Python objects at boundaries,
- stable serialization to dict/JSON,
- structured errors and predictable coercions.

For this project, Pydantic is used to ensure input/output correctness and keep parsing logic deterministic.

---

## 7. Future extensions (non-goals for the challenge)

Potential additions in a “real” service:
- Structured logging (e.g., `structlog`)
- SBOM / supply-chain tooling (SPDX, pip-audit)
- Async runtime (if requirements change)
- Observability exporters

These are intentionally out of scope unless explicitly required.
