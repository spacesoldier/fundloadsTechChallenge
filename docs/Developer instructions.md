You are working in a local repository that already contains substantial documentation under `docs/`. Your job is to implement the solution **strictly following the documentation-first + TDD process**.

## 0) Prime directives

1. **Documentation is the source of truth.** Before implementing anything, read and follow:

- `README.md`
- `docs/Challenge task.md`
- `docs/Solution.md`
- `docs/implementation/architecture/*`
- `docs/implementation/domain/*`
- `docs/implementation/steps/*`
- `docs/implementation/ports/*`
- `docs/implementation/kernel/*` (or equivalent kernel docs)
- `docs/analysis/data/*` (reference outputs and assets)

2. **TDD only.** For each unit (module/step/port), write tests first. Only then write code that makes them pass.
3. **One change at a time.** Make small commits: add tests, then implementation, then refactor.
4. **Determinism is mandatory.** Preserve NDJSON line order as the canonical order, per docs.
5. **No framework magic.** No decorators/metaclasses to trigger business logic implicitly. Explicit wiring and visible composition only.
6. **Config defines composition; code defines meaning.** Configuration selects steps and parameters. Configuration must not contain business logic.
7. **Single-threaded, per-message end-to-end execution.** The runner processes a full scenario per message, left-to-right.

8. **Port taxonomy is stable.** Use framework port types (`stream`, `kv_stream`, `kv`, `request`, `response`) and avoid creating project-level domain ports.

9. **If API is richer than base ports, use a service, not a new port.**  
   Multi-method domain convenience APIs are modeled as services and injected via `inject.service(...)`.

10. **Control-plane separation.**  
    Runner/Router are runtime control-plane components by default, not business DAG nodes.
    If modeled as nodes later, they must live in a dedicated platform control graph with explicit queues.

---

## 1) What to build (high-level)

Implement a streaming decision engine for fund load attempts:

- Read NDJSON events (one JSON object per line)
- Parse and normalize fields (notably money formats like `$123.00`, `USD123.00`, `USD$123.00`)
- Compute time keys and features
- Apply idempotency gate (duplicate/conflicting ids)
- Evaluate policies based on configured mode (baseline vs experimental config)
- Update window state (daily/weekly sums + attempt counts + prime global gate when enabled)
- Produce output JSON lines exactly as required by the challenge
- Write `output.txt`

Implementation must follow the step specs in:  
`docs/implementation/steps/01 ParseLoadAttempt.md` ... `08 WriteOutput.md`

---

## 2) Repository structure to implement

Create/maintain these top-level dirs:

- `src/` for all application code
- `tests/` for pytest tests mirroring the `src` structure

Suggested internal structure (adapt to existing docs if they already prescribe one):

src/  
domain/  
contracts/  
kernel/  
ports/  
adapters/  
usecases/  
config/  
cli/

Key rule: imports point inward.

---

## 3) Starting point: parsing input (Step 01)

### Step 01: ParseLoadAttempt

- Implement NDJSON parsing using `json.loads(line)`.
- Validate and normalize with Pydantic (preferred).
- Store:
    - raw string fields (for trace/debug)
    - normalized numeric amount (Decimal)
    - currency (default USD; optionally extracted)
    - parsed timestamp (UTC-aware datetime)
- Must accept money formats:
    - `$1234.00`
    - `USD1234.00`
    - `USD$1234.00`
    - optionally tolerate spaces around tokens
- If parsing fails: follow the documented error policy (fail closed vs drop vs error output). Ensure behavior is deterministic and test-covered.

### Tests first

Write tests for:
- parsing valid record (given sample from `docs/analysis/data/assets/input.txt`)
- parsing each money format variant
- invalid money format -> expected failure mode
- invalid JSON line -> expected failure mode

---

## 4) Then implement in this order (TDD)

Follow step specs and implement in strict sequence:

1. `ParseLoadAttempt`
2. `ComputeTimeKeys`
3. `IdempotencyGate`
4. `ComputeFeatures` (including prime id feature and “Monday multiplier” / experimental rules)
5. `EvaluatePolicies` (baseline + experimental mode)
6. `UpdateWindows`
7. `FormatOutput`
8. `WriteOutput`

For each step:
- create a spec-aligned unit test module
- implement step code to satisfy spec
- add integration tests for scenario-level behavior once several steps exist

---

## 5) Kernel runtime

Implement kernel components as specified:
- `Context` (mutable metadata, trace tape)
- `Step` contract `(msg, ctx) -> Iterable[msg]`
- `Scenario` (immutable list of bound steps)
- `Runner` (single-threaded, per-message end-to-end, worklist semantics)
- `StepRegistry` (composition-time only)
- `ScenarioBuilder` (builds Scenario from config using StepRegistry)
- `CompositionRoot` (wires ports/adapters + config + scenario)

Add kernel-level tests:
- deterministic order
- fan-out ordering
- drop semantics
- trace recording order
- exception path policy

---

## 6) Ports and adapters

Implement ports per docs:
- `InputSource` (NDJSON file reader adapter first)
- `OutputSink` (writes output lines to a file first)
- `PrimeChecker` (baseline: deterministic primality check; may cache)
- `WindowStore` (in-memory store for this challenge; used both in tests and runtime)

Adapters implement ports; steps call ports only when IO/state is the step’s purpose (UpdateWindows, WriteOutput, etc.).

---

## 7) Baseline vs experimental configs

The same pipeline should run under two configs and produce two different outputs:
- baseline output (matches `docs/analysis/data/assets/output.txt`)
- experimental output (matches `docs/analysis/data/assets/output_exp_mp.txt`)

Add integration tests that:
- run the full scenario for a small subset fixture
- run the full scenario for full input (optional if fast enough)
- compare produced output with reference assets

---

## 8) Deliverables

- `src/` implementation
- `tests/` full suite
- `output.txt` produced by CLI command
- CLI entrypoint that runs the chosen scenario/config on input file and writes output

---

## 9) Constraints / style

- Use `Decimal` for money.
- Use timezone-aware datetimes, normalize to UTC.
- No DB required for runtime.
- Avoid heavy dependencies beyond: pydantic + pytest.
- Keep each step single-responsibility and testable.

Proceed now:
1. Read docs listed above.
2. Scaffold poetry project and test runner.
3. Implement parsing step + tests.
4. Move to subsequent steps.

Return progress as:
- what docs were read (paths)
- what tests were added
- what code was added
- how to run tests and generate output
