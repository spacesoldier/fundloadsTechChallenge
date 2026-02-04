Implementation Report
=====================

Overview
--------
This document summarizes the implementation work completed for the funds load
adjudication engine, and provides repeatable verification steps. It is meant
as a concise, executable checklist rather than a narrative log.

Primary goals addressed
-----------------------
- Deterministic decisions for a given input file.
- Reproducible reference output generation.
- Strict input order preservation in output.
- Test-driven, documented implementation per the domain/step specs.

What was implemented
--------------------
- Domain types and parsing/normalization for load attempts.
- Step-by-step pipeline: Parse -> TimeKeys -> Idempotency -> Features -> Policy
  evaluation -> Window updates -> Output formatting -> Output writing.
- Kernel runtime: Context, Step contract helpers, Scenario, Runner, Registry,
  ScenarioBuilder, CompositionRoot.
- Ports and adapters: InputSource, OutputSink, PrimeChecker, WindowStore,
  TraceSink (JSONL/STDOUT).
- Config models + loader + wiring from config to registry/steps.
- CLI entrypoint with config overrides (output path and tracing flags).
- Tracing: TraceRecord/Recorder, optional sinks, and integration into Runner.
- Integration tests for baseline and experimental scenarios.

Notable decisions and doc conflicts (explicitly handled)
--------------------------------------------------------
- Output JSON field order: enforced as id, customer_id, accepted (matches
  Challenge task examples).
- JSON output spacing: compact (no extra spaces) for byte-for-byte diff.
- jsonb output in Postgres is non-deterministic in key order and spacing; the
  reference SQL now uses explicit format(...) to lock ordering/spacing.
- Tracing is configured via `runtime.tracing`; CLI flags override config.

Verification commands
---------------------
1) Run all tests:

```
poetry run pytest --cov=src -q
```

2) Produce baseline output:

```
poetry run python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output output.txt
```

3) Produce baseline output with tracing:

```
poetry run python -m fund_load \
  --config src/fund_load/baseline_config_newgen.yml \
  --input docs/analysis/data/assets/input.txt \
  --output output.txt \
  --tracing enable \
  --trace-path trace.jsonl
```

4) Compare output with reference (strict diff):

```
diff -u output.txt docs/analysis/data/assets/output.txt
```

5) Compare output with reference (JSON-normalized):

```
jq -cS . output.txt > /tmp/out.norm
jq -cS . docs/analysis/data/assets/output.txt > /tmp/ref.norm
diff -u /tmp/out.norm /tmp/ref.norm
```

6) Pretty-print trace JSONL for inspection:

```
jq -C . trace.jsonl | less -R
```

Reference output generation notes
---------------------------------
The SQL reference output in `docs/analysis/data/scripts/generate_ref_outs.sql`
was updated to use explicit `format(...)` for deterministic key order and
spacing, which aligns with the program output.

Files of interest
-----------------
- CLI/runtime: `src/stream_kernel/app/runtime.py`
- CLI flags: `src/stream_kernel/app/cli.py`
- Tracing: `src/stream_kernel/kernel/trace.py`
- Output formatting: `src/fund_load/usecases/steps/format_output.py`
- Baseline config: `src/fund_load/baseline_config_newgen.yml`
- Experimental config: `src/fund_load/experiment_config_newgen.yml`
- Reference outputs: `docs/analysis/data/assets/output.txt`,
  `docs/analysis/data/assets/output_exp_mp.txt`
