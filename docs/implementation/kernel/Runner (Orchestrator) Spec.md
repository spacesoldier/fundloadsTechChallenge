
The **Runner** is the kernel component that executes a configured **Scenario** over a stream of input events, producing output events.  
For this challenge the runner is **single-threaded** and **deterministic**.

This document also clarifies how the runner relates to **Step Registry**: the runner **does not** use the registry directly; it executes a **scenario that has already been built** using the registry by the service composition root.

---

## 1. Responsibilities

The runner:

1. Pulls raw events from `InputSource`.
2. Creates a `Context` for each raw event.
3. Executes the `Scenario` step-by-step **end-to-end per message**.
4. Records tracing information (optional, but supported by design).
5. Ensures output order is deterministic (stable emission order).
6. Completes the lifecycle for each message (finalize context, flush trace if configured).

The runner does not:

- interpret business rules (belongs to steps such as `EvaluatePolicies`),
- store business state (delegated to ports like `WindowStore` via dedicated steps),
- perform IO directly (IO happens via ports invoked from specific steps),
- resolve step names from config (registry responsibility, composition-time),
- construct steps (composition-time).

---

## 2. How Runner interacts with Step Registry

### 2.1 Composition-time vs runtime

**Step Registry is composition-time infrastructure.**  
It exists so the **service composition root** can turn config into executable objects.

- **Registry input**: step keys + step-local config (from YAML)
- **Registry output**: step instances (or factories) and validated/bound parameters
- **Scenario output**: a fully built, immutable `Scenario` object:
    - ordered list of bound step callables
    - step names/ids (for tracing)
    - scenario metadata (name, version, mode)

**Runner runtime input**: the built `Scenario`.  
The runner never reads YAML and never calls registry APIs.

### 2.2 Why Runner does not call the registry

- keeps the kernel minimal and deterministic,
- avoids hidden dynamic behavior at runtime,
- ensures all validation/binding happens once, upfront,
- makes runner tests independent of configuration parsing.

### 2.3 Failure modes and where they belong

- Invalid config / unknown step name → **registry/build-time error** (fail fast before any execution).
- Missing dependency (port not bound) → **registry/build-time error**.
- Step runtime exception (bug / unexpected input) → **runner runtime error policy**.

---

## 3. Execution model

### 3.1 End-to-end per message (depth-first)

For each incoming event, the runner executes the full scenario from step 1 to step N before moving to the next input event.

**Why**

- simplest deterministic stream semantics,
- matches “NDJSON line order defines canonical order” assumption,
- straightforward for window updates and idempotency ordering.

### 3.2 Worklist per step (fan-out support)

Because steps may emit 0/1/N messages, the runner maintains a worklist:

- Start: `work = [initial_message]`
- For each step:
    - `next_work = []`
    - For each msg in `work`, extend `next_work` with the step output
    - `work = next_work`
    - if `work` becomes empty → early stop

Scenario progress is explicit:
- current step index `i`
- current `work` list

**Ordering rule:** within a single input event, preserve:
- worklist order, and
- per-step emission order.

---

## 4. Structure

### 4.1 Key types (conceptual)

- `Scenario`: ordered list of **bound** steps (step callable + name/id + optional metadata)
- `Step`: callable `(msg, ctx) -> Iterable[msg]`
- `ContextFactory`: creates `Context` for a raw input
- `TraceRecorder`: records step enter/exit, message signature, ctx diffs
- `InputSource`: yields raw input events (lines, records, etc.)
- `OutputSink`: accepts final output lines/records

### 4.2 Runner dependencies

The runner is constructed by the **service composition root**, which provides:
- `scenario` (built from config via Step Registry)
- `input_source` implementation (adapter)
- `output_sink` implementation (adapter)
- tracing options (on/off, diff whitelist, signature mode)
- `context_factory` (often a simple constructor)

Runner remains generic: no task-specific logic.

---

## 5. Control flow (logic narrative)

For each item from `InputSource`:

1. `ctx = ContextFactory.from_raw(raw, line_no=...)`
2. Initialize: `work = [raw]`  
    (If the first step is `ParseLoadAttempt`, it converts raw → typed message. Runner is indifferent.)
3. For each bound step in `Scenario.steps`:
    - `next_work = []`
    - For each `msg` in `work`:
        - `TraceRecorder.on_step_enter(step_name, msg, ctx)`
        - `out_iter = step(msg, ctx)`
        - `TraceRecorder.on_step_exit(step_name, msg, ctx, out_iter)`
        - append outputs to `next_work`
    - `work = next_work`
    - if `work` empty: break
4. Finalization:
    - If the scenario produces final outputs as messages, runner forwards them to `OutputSink`
    - If the scenario includes a final “write” step (`WriteOutput`), then `work` may become empty and that is acceptable

**Runner invariants**
- stable output order
- per-input processing is isolated (new context per input)
- strict input order (no concurrency), so state updates occur in canonical order

---

## 6. Tracing and context change logging

### 6.1 What runner records

Per step, per message:
- step name/id
- enter timestamp / exit timestamp / duration
- message signature before/after (type name, or stable hash)
- selected context keys diff (whitelist)

### 6.2 Where stored

- Primary: `ctx.trace` (in-memory list)
- Optional: `TraceSink` (adapter) writing JSONL traces

Runner owns recording calls; steps stay clean.

---

## 7. Error handling policy

Runner enforces predictable error behavior:

- If a step throws:
    - record the exception in `ctx.errors` and/or `ctx.trace`
    - apply a documented policy

Recommended for this challenge:

- **Fail closed**: if we can still produce a valid output line for this input, produce a `DECLINED` decision (with an internal reason like `INTERNAL_ERROR`) and continue.
- If the failure occurs before we can even identify the output record (e.g., parsing cannot recover): stop with a clear error, unless an explicit “parse failure output” mode is configured.

The key requirement: the policy is consistent and testable.

---

## 8. Test suite for Runner

Runner tests are kernel-level behavior tests, using dummy steps and dummy ports.

### 8.1 Deterministic order

Given two messages A then B and a scenario with marker steps, expect:

- A fully processed before B
- outputs ordered `[A_out, B_out]`

### 8.2 Worklist fan-out

Given a step that emits `[m1, m2]` and a later mapping step, expect:
- outputs preserve emission order `[m1', m2']`

### 8.3 Drop semantics

Given a step that returns empty iterable, expect:
- runner stops early for that message
- no output produced (unless “write step” semantics are used)

### 8.4 Early-stop isolation

Given message A dropped at step2 and message B passes, expect:
- B runs full scenario and outputs normally

### 8.5 Trace recording shape

With tracing enabled, expect:
- `ctx.trace` has one record per executed step
- step names follow scenario order
- durations are non-negative

### 8.6 Context diff whitelist

Given step mutates multiple context keys, expect:
- only whitelisted keys appear in trace records

### 8.7 Exception path

Given a step raises, expect:
- `ctx.errors` includes exception info
- trace contains a “failed step” record
- runner applies the chosen error policy consistently

---

## 9. Notes on breadth-first execution

Runner intentionally does not do breadth-first (step1 for all, then step2 for all) because it:
- requires buffering between steps,
- complicates window semantics and idempotency,
- increases memory usage with no benefit for this task.

If needed later, it should be a separate runner implementation.

---

End of Runner spec.