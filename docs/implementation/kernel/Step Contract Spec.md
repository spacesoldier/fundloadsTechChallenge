# Step Contract Spec

**Status:** Draft (implementation-target)  
**Scope:** Kernel contracts (runtime), not business logic  
**Implementation:** [step.py](../../../src/fund_load/kernel/step.py)  
**Goal:** define what a “step” is, how it is executed, what it may return, and how we test it.

A **Step** is the smallest executable unit in a Scenario.  
It transforms a stream of messages **left-to-right** under a single uniform calling convention.

---

## 1. Step purpose

A step exists to do exactly one conceptual thing:

- parse
- enrich
- filter
- evaluate
- write side-effects (only when explicitly “IO step”)

A step is **not**:
- a “god function” with branching soup
- a hidden callback with magic lifecycle
- a framework hook

---

## 2. Core contract

### 2.1 Signature

A Step is a callable with this contract:

- **Input:** `(msg: In, ctx: Context) -> Iterable[Out]`

Properties:
- may output **0** messages (drop)
- may output **1** message (map)
- may output **N** messages (fan-out)
- must be deterministic given:
  - input message,
  - context values it reads,
  - ports it calls,
  - config it was bound with.

### 2.2 Allowed side effects

Steps may:
- mutate **Context** (within allowed namespaces)
- call ports **only if that step’s responsibility includes IO/state**, e.g.:
  - `UpdateWindows` (WindowStore)
  - `WriteOutput` (OutputSink)

Steps should not:
- perform hidden IO (network, filesystem) unless explicitly an IO step
- keep cross-event state (no global caches unless they are ports/adapters)

### 2.3 Message immutability rule

Steps must treat incoming messages as immutable value objects.

If a step needs to “change” the message:
- create a new message instance (or an enriched wrapper),
- do not mutate input message in place.

---

## 3. Step classification (for documentation + linting)

We classify steps by intent:

### 3.1 Pure step
- Reads message + ctx
- Returns new message(s)
- Does not call ports
- Does not depend on external state

Examples:
- `ParseLoadAttempt` (if we treat parsing as pure)
- `ComputeFeatures` (pure compute)

### 3.2 IO step
- Calls ports/adapters
- Performs side effects (store windows, write output)

Examples:
- `UpdateWindows`
- `WriteOutput`

### 3.3 Gate step
- Determines whether message continues
- Returns `[]` to drop, or `[msg]` to pass (or special message for “dead letter”)

Example:
- `IdempotencyGate`

This classification is not a runtime mechanism by default, but it is useful for:
- documentation clarity
- optional static checks (later)

---

## 4. Standardized return semantics

All steps must return an **iterable**.

### 4.1 “Drop”
- return `[]` or `()` or an empty iterator

### 4.2 “Map”
- return `[new_msg]`

### 4.3 “Fan-out”
- return `[msg1, msg2, ...]`

### 4.4 “Pass-through”
- return `[msg]` unchanged (allowed but discouraged unless the step is a Tap-like observer)

### 4.5 “Fail closed” as a step behavior
If business requires “fail closed”, we do it explicitly:
- either return a Declined output message,
- or set a flag in ctx and let a later step produce the decline.
The kernel itself must remain generic; exception policy belongs to Runner + explicit steps.

---

## 5. Step errors

A step can fail in two ways:

### 5.1 Controlled failure (preferred)
- Step records an error into ctx (`ctx.error(...)`)
- Step returns a safe output (decline) or drops to an error stream

### 5.2 Exception (allowed but handled by runner)
- Step raises
- Runner catches, logs into ctx, applies configured error policy

**Rule:** Steps should prefer controlled failures when they can still emit a valid output line.

---

## 6. Binding configuration and dependencies

A step in the Scenario is a **bound step**:
- step implementation + validated step-local config + injected dependencies

Two important consequences:
1. The runner should only see callables ready to run.
2. Config parsing and dependency wiring must happen outside runtime (composition time).

---

## 7. Base combinators (Ruby-ish, explicit, no magic)

We provide minimal wrappers that *are themselves steps*.

### 7.1 Map
Transforms each message into exactly one message.

- Input: `(msg, ctx)`
- Output: `[fn(msg, ctx)]`

### 7.2 Filter
Passes or drops.

- If `pred(msg, ctx)` true: `[msg]`
- Else: `[]`

### 7.3 Tap
Side effects without changing the message.

- Run `fn(msg, ctx)`
- Return `[msg]`

### 7.4 FlatMap (optional)
- Output iterable is produced by `fn(msg, ctx)` directly
- Useful when one “logical step” is defined as a fan-out operation

> We can implement these as small dataclasses for explicitness.

---

## 8. Optional: Step metadata (recommended)

For tracing, each step should have:
- `name: str` stable identifier (used in config and trace records)

We can enforce via:
- `NamedStep` wrapper that pairs `(name, callable)`
- or step objects with `.name`

---

## 9. Testing specification

These tests are about **step contract correctness**, not business rules.

### 9.1 `test_step_returns_iterable`
**Given:** a step implementation  
**When:** executed  
**Expect:** return value is iterable  
(At minimum, supports `iter(out)` without error.)

### 9.2 `test_drop_semantics`
**Given:** a Filter step that returns empty  
**Expect:** out is empty iterable

### 9.3 `test_map_semantics`
**Given:** a Map step  
**Expect:** exactly one output message

### 9.4 `test_fanout_semantics`
**Given:** a step emitting `[a, b, c]`  
**Expect:** iterable yields three outputs in the defined order

### 9.5 `test_step_may_mutate_context`
**Given:** Tap that increments a metric  
**Expect:** message unchanged, `ctx.metrics["x"]` updated

### 9.6 `test_step_must_not_mutate_message_in_place`
**Given:** an input message object  
**When:** step “changes” it  
**Expect:** returned message is not the same object (or equals old but is a new instance)

How to test:
- for dataclasses: compare `id(obj)` or frozen behavior
- for dict-based messages: enforce a “frozen” wrapper or copy-on-write policy

### 9.7 `test_controlled_failure_records_error`
**Given:** parsing step with invalid raw input  
**Expect:**
- `ctx.errors` appended
- step returns either decline message or drops to error stream (depending on spec)

### 9.8 `test_exception_failure_is_not_swallowed_by_step`
**Given:** a step that raises  
**Expect:** exception bubbles (runner handles it)
(We test runner policy separately; this test ensures steps don’t silently swallow unexpected exceptions.)

---

## 10. Testing combinators

### 10.1 Map
`test_map_outputs_one_value`
- map function called exactly once per input
- output list length == 1

### 10.2 Filter
`test_filter_passes_or_drops`
- pred true => output contains the same msg
- pred false => output empty

### 10.3 Tap
`test_tap_preserves_message`
- tap called
- message unchanged
- context mutated as expected

### 10.4 FlatMap
`test_flatmap_preserves_order`
- fn returns `[m2, m3]`
- outputs yielded in that order

---

## 11. Contract “gotchas” (must be documented)

1. **A step must never return `None`.**
   - `None` is a contract violation.
2. **A step must never return a single message directly.**
   - must be wrapped: `[msg]`.
3. **A step must not leak generator re-use bugs.**
   - if returning a generator, it must be fresh per call.
4. **Steps must be side-effect explicit.**
   - if it calls ports, it must be documented as IO step.

---

## 12. Summary

A Step is a callable:
- `(msg, ctx) -> Iterable[msg]`
- 0/1/N outputs unify drop/map/fan-out
- messages remain immutable, context may mutate
- IO only in explicitly designated steps
- minimal combinators provide Ruby-like expressiveness without framework magic
