
This document defines the **kernel**: a small, generic runtime that executes a configured scenario over a stream of messages.  
The kernel owns orchestration, context lifecycle, and observability hooks. It contains **no business rules**.

---

## 1. Kernel responsibilities

The kernel is responsible for:

- **Orchestration**: execute a scenario step-by-step, left-to-right.
- **Context lifecycle**: create, propagate, and finalize a `Context` per input event.
- **Determinism**: stable ordering, repeatable results, predictable error behavior.
- **Tracing / observability**: record what happened at each step (in-memory trace and/or optional sink).
- **Execution contract**: provide a uniform step calling convention and fan-out/drop semantics.
- **Progress bookkeeping**: keep “where we are” explicit (step index + worklist).

The kernel is explicitly **not** responsible for:

- Evaluating business policies (domain logic).
- Holding long-lived business state (delegated to `WindowStore` via ports).
- Doing IO beyond what is necessary to run the scenario (IO happens in adapters via ports, and only in designated steps).
- Discovering or instantiating steps from configuration (composition-time work, owned by the service via `Step Registry` and `Scenario Builder`).
- Owning application startup concerns (paths, env, config files, CLI). That is handled by the **composition root**.

---

## 2. Moving parts

### 2.1 Scenario

A **scenario** is an immutable, ordered list of **bound steps** (step implementation + validated step-local config).

- The scenario is the unit of execution (“run this flow”).
- Configuration selects:
  - step order
  - step parameters
  - feature flags / mode switches
- Code defines the meaning of each step.

The scenario contains **no registry**, no adapters, and no composition logic.
More about [Scenario](./Scenario%20Spec.md) and [Scenario builder](./ScenarioBuilder%20Spec.md)

### 2.2 Runner (Orchestrator)

The **runner** is the execution loop:

- Reads events (or already-parsed messages) from `InputSource`
- Creates a fresh `Context`
- Executes the scenario end-to-end for that event
- Emits final outputs to `OutputSink`

The runner is intentionally minimal and deterministic for this challenge.  
More about [Runner (Orchestrator)](./Runner%20(Orchestrator)%20Spec.md).

### 2.3 Step contract

A step is a callable with a single explicit contract:

- Input: `(message, context)`
- Output: an iterable of messages (0, 1, or many)

This lets one step:

- drop a message (`return []`)
- transform (`return [new_message]`)
- fan-out (`return [a, b, c]`)

Steps may read/write context. Steps may call ports **only when IO is the step’s purpose** (e.g., `UpdateWindows`, `WriteOutput`).

More about [Step contract](./Step%20Contract%20Spec.md)

### 2.4 Context

`Context` is mutable, short-lived metadata for one message execution:

- trace identifiers
- input line number (or event offset)
- timestamps
- debug tags
- counters/metrics
- errors / notes

Business meaning stays out of `Context`.  
The kernel may attach a trace tape of step execution to it.
More about [Context](./Context%20Spec.md)

### 2.5 Trace / context change log

We record what happened during execution in a structured way:

- step entered / step exited
- message signature before / after (type and/or hash)
- context diff (selected keys only)
- timing (durations)

Trace storage options:

- **Primary**: in-memory `ctx.trace` (good for tests and debugging).
- **Optional**: a `TraceSink` (adapter) that can write JSONL to a file or stdout.

The kernel owns the tracing mechanism; steps just mutate context normally.

More about [tracing and context logging](./Trace%20and%20Context%20Change%20Log%20Spec.md)

### 2.6 Step Registry (composition-time)

**Step Registry** is intentionally **not part of the runtime kernel loop**.

It is used by the **composition root** (via the Scenario Builder) to:

- resolve step keys from config into factories/implementations,
- validate step-local configuration,
- bind step dependencies (ports, constants, toggles),
- produce an immutable `Scenario` object for the runner.

The runner executes the scenario and remains unaware of registries.

More about [Step Registry Spec](./Step%20Registry%20Spec.md).

### 2.7 Scenario Builder (composition-time)

The **Scenario Builder** is also **not part of the runtime kernel loop**.

It consumes:

- a validated flow configuration (step list + step-local config),
- a Step Registry,
- a dependency bundle (ports, feature toggles, tracing knobs if needed),

and produces:

- a `Scenario` (ordered, bound step instances),
- with stable step names/ids for tracing and debugging.

More about [Scenario Builder Spec](./Scenario%Builder%20Spec.md).

### 2.8 Composition Root (startup wiring)

The **Composition Root** is the single place where the application is assembled:

- load/validate config,
- build adapters and expose them as ports,
- create Step Registry,
- build Scenario via Scenario Builder,
- create Runner,
- start execution.

The kernel does not depend on the composition root; it only consumes the final `Scenario` + ports passed into bound steps.

More about [Composition Root Spec](./Composition%20Root%20Spec.md).

---

## 3. Execution strategy in a single-threaded program

There are two high-level ways to execute a pipeline over many messages:

### Strategy A: per message end-to-end (depth-first)

For each input event:

1. run step 1  
2. run step 2  
3. …  
4. run step N  
5. emit output  

This is the default for this project.

**Why it fits this task**

- Deterministic and easy to reason about
- Natural for streaming semantics
- Window state updates happen in a clear order aligned with input ordering
- Trace per message is straightforward

### Strategy B: step-by-step across all messages (breadth-first)

Run step 1 for all messages, then step 2 for all messages, etc.

This is closer to batch engines, but it complicates:

- window semantics (needs buffering)
- idempotency/conflict routing (needs global coordination)
- memory usage (messages must be retained between steps)

For this challenge we intentionally **do not use Strategy B**.

**Kernel decision:** use Strategy A (message end-to-end).

---

## 4. Control flow and “where am I in the scenario?”

The kernel tracks execution with:

- current step index `i`
- current “worklist” at that step (because a step can emit N messages)

Pseudo-flow:

1. Start with a list containing the initial message: `work = [msg]`
2. For each step `S[i]` in scenario:
   - `next_work = []`
   - for each `m` in `work`:
     - run `S[i](m, ctx)` → iterable
     - extend `next_work`
   - set `work = next_work`
   - if `work` becomes empty: stop early (dropped)
3. Final `work` is the scenario output (usually exactly one formatted output message)

This keeps progress explicit: `i` and `work`.

---

## 5. Where step-by-step progress is stored

The kernel is the only component that can reliably say:

- which step ran
- in what order
- what was the message identity before/after
- what context keys changed

So the kernel stores progress in `ctx.trace` as a list of records, e.g.:

- `StepTraceRecord(step_name, t0, t1, msg_before_sig, msg_after_sig, ctx_diff)`

Tracing is configurable:

- enabled/disabled
- context diff keys are whitelisted
- message signatures can be “type only” or “hash”

---

## 6. Minimal configuration relevant to the kernel

The kernel runtime itself needs no business configuration. It only needs:

- tracing options (on/off, verbosity)
- (optionally) scenario identifier for trace correlation

Composition-time configuration lives outside the kernel and is consumed by the service when building the scenario via Step Registry + Scenario Builder:

- scenario selection (which flow)
- step order and parameters

Constraints on config:

- no arbitrary expressions
- no inline `if/else` blocks
- no dynamic code execution

Configuration defines composition; code defines meaning.

---

## 7. Testing expectations for the kernel

Kernel tests focus on invariants:

- deterministic execution order
- correct fan-out semantics
- correct “drop” behavior
- correct trace recording (presence + step order)
- correct early-stop behavior when a message is dropped

Kernel tests do **not** test business policies.

---

## 8. Summary

The kernel is a small deterministic runtime that:

- executes a configured scenario left-to-right
- runs end-to-end per message (stream semantics)
- carries immutable messages + mutable context
- owns tracing and context-diff logging
- keeps domain and infrastructure concerns outside the kernel runtime
- relies on the service for composition-time step binding via:
  - Step Registry
  - Scenario Builder
  - Composition Root
