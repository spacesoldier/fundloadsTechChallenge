## Intro

This directory contains **port specifications** — stable interfaces that isolate the core/usecases from IO, storage, and environment-specific concerns.

Ports define:
- **what** the system needs (capabilities / contracts),
- **not** how it is implemented.

Implementations live in **adapters** and may change freely without affecting:
- domain model,
- kernel runtime,
- step logic (usecases), as long as port contracts remain stable.

---

## Ports in this project

- [Input Source](./InputSource.md)  
  Provides a stream of raw input events (NDJSON lines for this challenge). Responsible for ordering and raw data delivery.

- [Output Sink](./OutputSink.md)  
  Accepts final output records (JSON lines). Responsible for writing to a destination (file/stdout/etc.).

- [Prime Checker](./PrimeChecker.md)  
  Provides a fast predicate for “is this id prime?” with a clearly defined numeric domain and caching strategy.

- [Window Store](./WindowStore.md)  
  Provides read/write access to windowed state (daily/weekly counters and sums) with well-defined atomicity expectations.

---

## Design rules (quick)

- Ports are **stable**: change ports rarely.
- Ports should be **minimal**: only operations that the usecase needs.
- Ports are **synchronous** in this challenge (single-threaded runtime).
- Ports must not leak adapter details (no DB cursors, file paths, HTTP clients).
- Errors must be **explicit** and testable (typed errors or well-defined exceptions).

---

## Testing expectations

Every port spec includes:
- contract invariants (preconditions / postconditions),
- required behaviors under edge cases,
- a minimal in-memory reference adapter strategy to support unit tests of steps and runner.

---

## Related docs

- Kernel and orchestration: see `docs/implementation/architecture/Kernel Overview.md`
- Step flow: see `docs/implementation/steps/Steps Index.md`
