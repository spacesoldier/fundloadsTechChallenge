#  Intro

This document defines the **OutputSink boundary**: how adjudication results leave the system.

Design goals:
- deterministic output (stable ordering, stable formatting)
- minimal responsibilities in the core
- future-proof integration (file today, queue/API tomorrow)

For the challenge, the required sink is:
- `output.txt` containing valid NDJSON (one JSON object per line)

---

## 1. Purpose and scope

OutputSink is responsible for:
- accepting already-formatted output lines (or output objects, depending on adapter),
- writing them in the correct order,
- ensuring durability semantics appropriate for the sink (as configured).

OutputSink is **not** responsible for:
- policy evaluation,
- window updates,
- business formatting rules (Step 07),
- idempotency decisions.

---

## 2. Output stream contract

### 2.1 Ordering
The system guarantees that outputs are produced in input order (`line_no` order).
The sink must preserve the order of received items.

### 2.2 One result = one output record
For each processed input event (canonical or non-canonical), the pipeline emits exactly one output record:

- `{"id":"...", "customer_id":"...", "accepted":true|false}`

Even duplicates/conflicts should produce an output record (declined with a reason internally),
unless the chosen spec says otherwise.

> Note: The challenge output format does not include reason codes, but the engine may keep them for tracing.

---

## 3. Port interface

Two common designs exist:

### A) Sink accepts text (NDJSON line)
- core produces `str` (already JSON)
- sink writes the line + newline

### B) Sink accepts structured object
- core produces `OutputRecord` dataclass
- sink serializes it

For this challenge we prefer **A**:
- Step 07 owns canonical JSON formatting,
- sink becomes a dumb writer,
- easier to test and reason about “valid JSON per line”.

Conceptual contract:

- `OutputSink.write(lines: Iterable[str]) -> None`

Or for streaming:
- `OutputSink.write_line(line: str) -> None`
- `OutputSink.close() -> None`

---

## 4. File sink adapter (challenge implementation)

### 4.1 Format requirements
- Output is NDJSON:
  - one JSON object per line
  - UTF-8
  - trailing newline after each line is allowed and recommended

### 4.2 Atomic write behavior (recommended)
To avoid partial files if the process crashes:

1) write to a temporary file (same directory), e.g. `output.txt.tmp`
2) flush + fsync
3) rename to `output.txt` (atomic on POSIX if same filesystem)

This is simple and production-like, while still minimal.

If you want ultra-minimal for challenge:
- direct write to `output.txt` is acceptable, but less robust.

We should document which mode is used.

### 4.3 Error handling
- Any I/O error is fatal and should terminate the run clearly.
- Partial output must not be silently produced.

---

## 5. Extensibility: future sinks

The same port can be backed by:
- Kafka producer (topic output)
- HTTP POST to another service
- S3 object upload
- database table writer (for audit)

In those cases:
- ordering semantics must be preserved by the adapter (or by upstream partitioning rules).

---

## 6. Testing strategy

### 6.1 Unit tests with in-memory sink
Provide a `CollectingOutputSink` for tests:
- stores received lines in a list
- allows assertions on:
  - count equals input count
  - order preserved
  - each line is valid JSON (optional)

### 6.2 File sink tests
Use a temp directory fixture:
- run sink write
- verify:
  - output file exists
  - number of lines matches expected
  - file is valid UTF-8
  - optional: atomic rename behavior (presence/absence of tmp file)

### 6.3 Determinism tests
Given the same inputs and config:
- emitted lines must be identical byte-for-byte
- (ordering and formatting are stable)

---

## 7. Non-goals

- schema registry
- compression
- encryption at rest
- batching/throughput optimizations

These can be added later behind the same port.

---
