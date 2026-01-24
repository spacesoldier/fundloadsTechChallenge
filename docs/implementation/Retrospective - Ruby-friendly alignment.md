# Retrospective - Ruby-friendly alignment

## Purpose

This note checks how the implemented solution aligns with:

- `docs/guide/Ruby-Friendly Python for Streaming Systems.md`
- The project documentation set (`docs/Challenge task.md`, `docs/Solution.md`,
  `docs/implementation/*`, `docs/analysis/*`)

It is intentionally brief and actionable.

## Alignment with the Ruby-friendly guide

### Strong alignment

- Step contract as a callable `(msg, ctx) -> Iterable[msg]` and step-specific message types
  map well to the "pipeline DSL" and "step contract" guidance (Parts III, IV).
- Ports and adapters are explicit and isolated (`ports/*`, `adapters/*`), avoiding
  "script soup" and hidden IO (Parts I, VI).
- Composition uses a registry + config-driven wiring, keeping configuration as
  composition rather than logic (Part III, "Registry + External Configuration").
- Deterministic, replayable behavior is prioritized (ordering preserved, deterministic
  output), matching the "clean room" and replay focus (Part III).
- Tests emphasize unit tests for steps, contract tests for ports, integration tests
  for flows (Part VII).
- Minimal magic: no framework-driven wiring or implicit behavior; explicit composition
  in `composition_root` and CLI (Parts I, II).

### Partial alignment / open gaps

- Message envelope vs payload is not fully modeled. We pass domain/usecase objects and
  keep metadata in `Context`, but we do not implement a distinct envelope type with
  offsets/headers (Part II, "Modeling the Message").
- Error signaling is minimal and follows the challenge requirements. We do not implement
  DLQ/route/retry strategies (Part III, "Error signaling policy"; Part V).
- Stateful window storage is in-memory only; persistence (e.g., Redis) is explicitly
  left as a port/adapter extension point (Part II, Part VI).
- Observability is present via tracing, but metrics and structured logs are not yet
  implemented (Part VII).
- Concurrency and backpressure are intentionally absent (single-threaded runner per
  documentation), so the "GIL-aware" patterns are out of scope here (Parts V, VI).

## Alignment with the project documentation set

### Strong alignment

- Deterministic output and strict input-order preservation are enforced.
- Idempotency behavior follows the documented gate and classification rules.
- Output schema and formatting match the challenge requirements (order: `id`,
  `customer_id`, `accepted`; no extra whitespace).
- Pipeline steps follow the documented order and responsibilities.
- Tests are TDD-driven per step and port, with integration tests for baseline and
  experimental configurations.
- Config composition is explicit and scoped (pipeline steps, features, policies,
  windows, output, tracing).

### Known constraints or deviations

- The design prioritizes the specified single-threaded, per-message execution; this
  intentionally avoids more complex concurrency patterns described in the guide.
- External storage (idempotency/windowing) is intentionally not implemented beyond
  in-memory adapters to keep the challenge reproducible.

## Notes for future extensions

- Introduce an explicit envelope type if multi-source ingestion or partitioned replay
  becomes a requirement.
- Add a minimal metrics port + adapter for throughput, drop reasons, and window sizes.
- Add a DLQ sink and structured error policy for malformed inputs if the runtime
  needs to be hardened beyond the challenge scope.

