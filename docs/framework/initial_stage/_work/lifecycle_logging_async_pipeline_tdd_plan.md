# Lifecycle logging async pipeline (TDD subplan)

## Goal

Move supervisor/worker lifecycle logging from blocking emit calls to a framework-native,
non-blocking pipeline with bounded memory and deterministic fallback.

Current state:

- lifecycle logs are emitted synchronously in supervisor/worker paths;
- `stdout`/`jsonl` writes are blocking operations.

Target state:

- lifecycle events are published into platform pipeline components;
- writing/export is decoupled from hot execution/control loops;
- `AsyncRunner` can host async exporter workers;
- sync fallback remains available for strict/minimal environments.

---

## Why this subplan exists

This is the logging counterpart of outbound API policy work:

- API calls need non-blocking policy/execution rails;
- lifecycle logging needs the same class of rails (queue, batching, retry/drop).

It should be implemented with the same platform model:

- system/service contracts;
- platform adapters;
- discovery + DI wiring;
- runner-aware execution profile (`sync` vs `async`).

---

## Architecture direction

### 1) System contracts

- `LifecycleLogPublisherService`
  - `publish(LogMessage) -> None`
- `LifecycleLogBufferService`
  - enqueue/dequeue semantics with bounded capacity.
- `LifecycleLogWriterService`
  - drains buffered events and calls concrete sink adapters.

### 2) Adapter layer

Adapter families:

- sync sinks: `stdout`, `jsonl`;
- async sinks: `httpx/aiohttp/otel logs otlp` (later phases);
- fallback sink: no-op/drop with counters.

### 3) Execution model

- phase baseline: publisher is non-blocking + bounded queue;
- writer loop can run:
  - in dedicated worker thread/process under sync rail;
  - in `AsyncRunner` process group under async rail.

### 4) Backpressure policy

Configurable policy per queue:

- `block` (bounded wait);
- `drop_newest`;
- `drop_oldest`.

All drop/block outcomes must emit counters.

---

## Runtime contract extension (draft)

`runtime.observability.logging.pipeline`:

- `enabled: bool` (default `false` initially);
- `mode: sync_direct | sync_buffered | async_buffered`;
- `queue.max_items: int`;
- `queue.backpressure: block | drop_newest | drop_oldest`;
- `flush.max_batch: int`;
- `flush.interval_ms: int`;
- `flush.timeout_ms: int`;
- `workers.profile: sync | async` (ties to process-group runner profile);
- `shutdown.drain_timeout_seconds: int`.

---

## TDD phases

### Phase A — contract freeze + validator

RED:

- invalid pipeline mode rejected;
- invalid backpressure policy rejected;
- invalid queue/flush numeric ranges rejected.

GREEN:

- normalized config defaults accepted.

### Phase B — non-blocking publisher + bounded buffer (sync)

RED:

- publisher path does not block beyond configured bound;
- overflow follows configured backpressure policy.

GREEN:

- in-memory bounded queue service wired via DI.

### Phase C — sync buffered writer loop

RED:

- queued events are flushed in batches;
- lifecycle stop drains queue within timeout;
- timeout produces deterministic warning/drop behavior.

GREEN:

- buffered sync writer integrated with current supervisor lifecycle.

### Phase D — AsyncRunner writer integration

RED:

- async writer profile cannot be bound to sync-only process group;
- async writer drains queue without blocking supervisor control path.

GREEN:

- writer service can be delegated to async process group/runner profile.

### Phase E — observability counters and diagnostics

RED:

- counters include published, written, dropped, retried;
- diagnostics expose queue depth and drop reasons.

GREEN:

- metrics/telemetry fields are emitted via platform observability rails.

### Phase F — regression/parity

RED:

- business output unchanged with pipeline disabled;
- deterministic behavior with fixed clock/input under enabled pipeline.

GREEN:

- parity report + perf characterization committed.

---

## Initial test catalog

- `LOG-PIPE-VAL-01` invalid `pipeline.mode` rejected.
- `LOG-PIPE-VAL-02` invalid `queue.backpressure` rejected.
- `LOG-PIPE-BUF-01` `drop_oldest` behavior is deterministic.
- `LOG-PIPE-BUF-02` `drop_newest` behavior is deterministic.
- `LOG-PIPE-SYNC-01` buffered writer flushes by batch and interval.
- `LOG-PIPE-ASYNC-01` async writer integration requires async runner profile.
- `LOG-PIPE-OBS-01` publish/write/drop counters emitted.
- `LOG-PIPE-REG-01` baseline output parity preserved.

---

## Relation to AsyncRunner

Yes: this subplan is intentionally aligned with `AsyncRunner` rollout.

- before AsyncRunner, we can ship `sync_buffered` mode;
- after AsyncRunner, `async_buffered` becomes primary for high-throughput exporters.

