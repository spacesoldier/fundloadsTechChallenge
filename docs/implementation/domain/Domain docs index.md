# Domain docs index

This directory describes the **domain vocabulary** used by the solution: core message shapes, semantics, and decision vocabulary.

The domain layer should stay **stable** and **pure**:
- no IO
- no adapters
- no orchestration/runtime concerns
- minimal dependencies (ideally stdlib-only)

## Documents

- [Message Types](./Message%20Types.md)  
  Canonical message models that flow through the pipeline (parsed attempts, enriched messages, decisions, output records).

- [Reason Codes](./Reason%20Codes.md)  
  Standardized decline reasons and how they are represented (internal vs output-facing).

- [Time and Money Semantics](./Time%20and%20Money%20Semantics.md)  
  UTC assumptions, day/week window keys, rounding/precision rules, and how “effective amount” is derived (e.g., Monday multiplier).

## How to use these docs

- When implementing a step, start by identifying which **domain message types** it consumes/produces.
- If a behavior is unclear, first check **Time and Money Semantics** and **Reason Codes** before inventing new rules.
- Keep domain changes deliberate: modifying these documents likely impacts tests and multiple steps.
