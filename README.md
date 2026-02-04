# Funds Load tech challenge

[![Tests & Coverage](https://github.com/spacesoldier/fundloadsTechChallenge/actions/workflows/coverage.yml/badge.svg?branch=main)](https://github.com/spacesoldier/fundloadsTechChallenge/actions/workflows/coverage.yml)
## Overview

This repository contains a solution to a technical challenge that models a **stream-oriented decision engine** for adjudicating fund load attempts under a set of regulatory and risk-control constraints.

The goal of the project is not only to produce correct accept/reject decisions, but to demonstrate a **clear, explainable, and extensible approach** to solving a stateful, rule-driven problem commonly found in financial and regulated systems.

The solution is intentionally structured and documented to reflect how similar problems are approached in real-world systems, while remaining strictly within the scope of the challenge.

---

## Problem Summary

The challenge models a simplified **fund load adjudication system**.

A stream of load attempts is processed sequentially, and for each attempt the system must decide whether it is **accepted or rejected** based on a fixed narrative of rules, including:

- **Velocity limits**:
    - maximum total load amount per day,
    - maximum total load amount per week,
    - maximum number of load attempts per day.
        
- **Calendar-based risk amplification**:
    - operations performed on specific days (e.g. Mondays) are treated as higher risk and counted with increased weight.
        
- **Identifier-based risk conditions**:
    - operations associated with specific identifier properties (e.g. prime-number identifiers) are subject to additional global and per-operation constraints.
        
- **Mixed scopes of enforcement**:
    - some rules apply per customer,
    - others apply globally across all customers.

Although each event is evaluated independently, decisions depend on **historical state accumulated over time windows**, making the problem inherently stateful and temporal.


---

## Repository Contents

At a high level, the repository consists of:
- documentation explaining the problem interpretation and solution design
- detailed guide about design and implementation principles
- tests validating correctness and edge cases
- source code implementing the decision pipeline 
- configuration files describing policy parameters

This README serves only as an entry point; detailed explanations are provided in the documents referenced below.

---

## References

- **Original challenge description**  
    [The full, unmodified text of the assignment](./docs/Challenge%20task.md)
- **Explanatory design note**  
    [A detailed explanation of the problem interpretation, assumptions, and solution structure](./docs/Solution.md)
    The design note explains how the narrative from the challenge was translated into a concrete processing model and why specific architectural decisions were made.
- **Data analysis**
	- [duplicates and conflicting uploads](./docs/analysis/data/Input%20data%20analysis%20-%20idempotency.md)
	- [reference output](./docs/analysis/data/Reference%20output%20generation.md)
- **Reasoning and assumptions**  
  [architectural reasoning and explicit assumptions](./docs/analysis/Reasoning%20and%20assumptions.md)
- **Detailed guide**
	- [table of contents](./docs/guide/Ruby-Friendly%20Python%20for%20Streaming%20Systems.md)
- **Pipeline steps**
  - [step index (pipeline reference)](./docs/implementation/steps/Steps%20Index.md)
- **Implementation**
  - [structure overview](./docs/implementation/Documentation%20structure.md)
  - [architecture overview](./docs/implementation/architecture/Architecture%20overview.md)
  - [implementation report](./docs/implementation/Implementation%20report.md)
- **Roadmap**
  - [development plan](./docs/implementation/Development%20plan.md)

---

## Notes for Reviewers

- The default configuration reproduces the rules described in the original challenge.
- Ambiguous aspects of the problem statement are resolved explicitly and documented.
- The focus is on clarity, correctness, and maintainability rather than feature breadth.
- **Using [Obsidian](https://obsidian.md/download) is strongly recommended** for better navigation across the project contents

## Prerequisites (minimal)

- Python 3.12+ installed (recommended via `pyenv` but not required).
- Poetry installed (`https://python-poetry.org/docs/#installation`).



### Example setup on Ubuntu / WSL (minimal)

```
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip curl
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
poetry install
```

## How to run (CLI)

The CLI is exposed via the module entry point. Example commands:

Baseline (default rules):
```
poetry run python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output output.txt
```

Experimental (Monday multiplier + prime-id gate):
```
poetry run python -m fund_load \
  --config src/fund_load/experiment_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output output_exp_mp.txt
```

Optional tracing (overrides config):
```
poetry run python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output output.txt \
  --tracing enable \
  --trace-path trace.jsonl
```

## Verify against reference output

Baseline:
```
diff -u output.txt docs/analysis/data/assets/output.txt
```

Experimental:
```
diff -u output_exp_mp.txt docs/analysis/data/assets/output_exp_mp.txt
```

If you need JSON normalization for diffing (should not be required):
```
jq -c . output.txt > output.normalized.txt
jq -c . docs/analysis/data/assets/output.txt > output.ref.normalized.txt
diff -u output.normalized.txt output.ref.normalized.txt
```

## Run tests

```
poetry run pytest --cov=src -q
```

## Reproducing reference output (SQL-based baseline)
Scripts live under:
- `docs/analysis/data/scripts/generate_ref_outs.sql`
- `docs/analysis/data/scripts/docker-compose.yml`
