# Trace & Context Change Log Spec

This document specifies the **tracing mechanism** in the kernel: how we record step-by-step execution in a structured form, how traces are stored, and how different **TraceSink adapters** can be configured (JSONL file now, OpenTelemetry later).

Tracing is **observability-only**. It must not affect business decisions.

---

## 1. Goals

Tracing must provide:

- **Step-level visibility**: entered/exited, timing, outcome.
- **Message evolution visibility**: what message type/signature was before/after each step.
- **Context visibility**: what selected context fields changed at each step.
- **Deterministic ordering**: trace records are ordered exactly as execution happened.
- **Low coupling**: steps never talk to trace sinks; the kernel owns tracing.

Non-goals:

- Storing full payloads by default.
- Building a full distributed tracing system (OTel is optional/future).
- Making tracing mandatory for correctness.

---

## 2. Architecture

### 2.1 Who produces trace records?

The **Runner (Orchestrator)** is the only component that can reliably emit trace records because it knows:

- which step ran,
- in what order,
- which message(s) were input/output of the step,
- timing boundaries.

Therefore, the runner emits trace records via a single interface:

- `TraceRecorder` (kernel-owned) produces `TraceRecord`s.
- `TraceSink` (port-like interface) consumes `TraceRecord`s.

Steps only mutate `Context` and return messages.

### 2.2 Storage locations

There are **two complementary storage targets**:

1) **In-memory trace tape** (always available when tracing enabled):
- `ctx.trace: list[TraceRecord]`

2) **External sink** (optional):
- `TraceSink.emit(record)` writes to file / stdout / OTel exporter, etc.

The in-memory tape is primarily for:
- deterministic tests,
- debugging a single run,
- attaching traces to “error outputs” if needed.

The external sink is primarily for:
- grep/jq workflows,
- long runs,
- external collection/analysis.

Both can be enabled at the same time.

---

## 3. Core Data Model

### 3.1 TraceRecord

A `TraceRecord` captures one “step invocation” for one message in the runner worklist.

Required fields:

- `trace_id: str` — stable ID for this input event’s execution (per Context)
- `scenario: str` — scenario name / flow id
- `line_no: int` — canonical input ordering (if applicable)
- `step_index: int`
- `step_name: str`

Timing:

- `t_enter: datetime` (UTC)
- `t_exit: datetime` (UTC)
- `duration_ms: float`

Message signatures:

- `msg_in: MessageSignature`
- `msg_out: list[MessageSignature]` (0..N outputs)
- `msg_out_count: int`

Context diff:

- `ctx_before: dict[str, Any]` (selected keys only, optional)
- `ctx_after: dict[str, Any]` (selected keys only, optional)
- `ctx_diff: dict[str, Any]` (optional: computed delta for selected keys)

Outcome:

- `status: "ok" | "error"`
- `error: ErrorInfo | None`

### 3.2 MessageSignature

A `MessageSignature` is intentionally small:

- `type_name: str` (e.g., `ParsedLoadAttempt`, `Decision`, `OutputLine`)
- `identity: str | None` (e.g., payment id, or derived stable id)
- `hash: str | None` (stable hash of canonicalized message representation)

Signature mode is configured (see below).

### 3.3 ErrorInfo

If the step raises, we capture:

- `type: str`
- `message: str` (truncated)
- `where: str` (step name + exception origin hint)
- `stack: str | None` (optional; default off to reduce noise)

---

## 4. Signature & Diff Policies

Tracing must be configurable to avoid leaking raw data and to keep volumes sane.

### 4.1 Message signature modes

`signature.mode`:

- `type_only`  
  Only `type_name`, no identity, no hash.

- `type_and_identity`  
  `type_name` + a stable identity extracted from message (`id_num`, etc.).

- `hash`  
  Adds a stable hash computed from a canonical representation.

Notes:
- Hashing must be deterministic across runs.
- Canonicalization must not include volatile fields (like timestamps generated at runtime).

### 4.2 Context diff modes

`context.diff.mode`:

- `none` — do not emit before/after/diff
- `whitelist` — include only listed context paths (recommended)
- `debug` — include broader sets (not recommended by default)

`context.diff.whitelist` example:
- `["line_no", "tags", "metrics", "errors", "decision.status", "decision.reasons"]`

We treat context as mutable; diff is computed by taking a “snapshot” of selected keys.

Rules:
- keys not in whitelist are ignored completely
- diff should treat nested dict keys either:
    - **flat keys only** (recommended now): whitelist contains top-level keys only
    - nested diffs can be v2 (more complexity than needed)
Default approach: **flat keys only**, with selected sub-structures stored as small dicts (like `ctx.tags` but only if you whitelist `tags` and keep it small).
---

## 5. Determinism and ordering

Trace records must be emitted in deterministic order:
For one input event:
- scenario step order: `step_index` increasing
- inside one step: `work_index` increasing in the order messages were processed in worklist
- output signatures list preserves the step emission order

Across input events:
- `line_no` increasing based on input order (if input is NDJSON file)
- runner processes one event end-to-end (depth-first), so records for line 1 come fully before line 2.

This guarantees grep patterns remain stable across reruns on same input/config.

---

## 6. TraceSink: single interface, multiple adapters

### 6.1 Interface

The kernel calls a single sink interface:

- `emit(record: TraceRecord) -> None`
- `flush() -> None` (optional)
- `close() -> None`

The runner may call `flush/close` on:
- end of run,
- exception shutdown path (best effort).

### 6.2 Adapter implementations

We define multiple **adapters** implementing the same sink interface:

1) **JsonlTraceSink** (default prototype)
2) **StdoutTraceSink** (debug convenience)
3) **OpenTelemetryTraceSink** (optional future / extra dependency)

Only one sink is selected by configuration at runtime, but the interface allows multiple if we later add a multiplexer.

---

## 7. JSONL File Sink (prototype)

### 7.1 Why JSONL

- One record per line → excellent for `grep`, `jq`, incremental processing.
- Deterministic order = execution order.
- Crash tolerance: already written lines survive process termination.

### 7.2 Output format

Each line is a single JSON object:

- `TraceRecord` serialized with stable key names
- timestamps in RFC3339 UTC, e.g. `2026-01-21T01:23:45.123Z`

No pretty printing. No multi-line objects.

### 7.3 Writing strategy: line-by-line vs micro-batching

We support both via config.

Default behavior: **line-by-line** with buffered IO.

Config knobs:

- `write_mode: "line" | "batch"`
- `flush_every_n: int` (default `1` in line mode; default `100` in batch mode)
- `flush_every_ms: int | null` (optional time-based flush)
- `fsync_every_n: int | null` (default null; avoid unless explicitly needed)

Semantics:

- In `line` mode:
  - each `emit()` writes one line and increments counter
  - `flush()` is called every `flush_every_n` records (default 1)
- In `batch` mode:
  - records are buffered in memory
  - written as a block when thresholds hit
  - **risk**: the last buffered chunk may be lost on crash

Recommendation for this challenge:
- Use `line` mode, `flush_every_n=1`, no fsync.

### 7.4 File path & rotation

We keep the prototype simple:

- `path: str` required
- open in append mode
- optional `rotate: none` (future)
- optional `max_bytes` (future)

For now: no rotation, deterministic name.

---

## 8. OpenTelemetry Sink (optional / future)

### 8.1 Why support OTel

- Integrates with standard observability stacks.
- Enables distributed tracing if later the runner becomes multi-process or remote.

This is **optional** and should be packaged as an extra dependency group.

### 8.2 Mapping TraceRecord → OTel span

For each `TraceRecord`, we create a span:

- span name: `step_name`
- start/end: `t_enter` / `t_exit`
- span kind: `INTERNAL`

Attributes (examples):
- `scenario`
- `line_no`
- `step_index`
- `trace_id` (our internal, as attribute unless we bridge)
- `msg_in.type`
- `msg_out.count`
- `status`

Events:
- `ctx_diff` as an event if enabled, but must be size-limited.

Error handling:
- if `status="error"`, mark span status error, record exception (OTel standard).

### 8.3 Trace context bridging

We have two modes:

- **Attribute-only (default)**: keep `ctx.trace_id` as a span attribute.
- **Bridge mode (future)**: generate OTel trace/span ids from our context and propagate them.

For this challenge, **attribute-only** is enough.

### 8.4 Exporters & configuration

The sink must support standard OTel exporter configuration (environment-driven is acceptable):

- OTLP exporter (HTTP or gRPC)
- service.name (e.g. `fund-load-tech-challenge`)
- optional resource attributes

We do not hardcode exporter details in business config; we only select “otlp” mode and let environment variables do the rest.

Packaging recommendation:
- `poetry extras` or optional dependency group:
  - `opentelemetry-api`
  - `opentelemetry-sdk`
  - `opentelemetry-exporter-otlp` (or http/grpc variant)

---

## 9. Configuration (how sinks are selected)

Tracing is configured in the scenario/service configuration, not inside steps.

Example conceptual config:

```yaml
tracing:
  enabled: true
  signature:
    mode: hash         # type_only | type_and_identity | hash
  context_diff:
    mode: whitelist    # none | whitelist | debug
    whitelist:
      - line_no
      - tags
      - metrics
      - errors

  sink:
    kind: jsonl        # jsonl | stdout | otel
    jsonl:
      path: "var/trace.jsonl"
      write_mode: "line"
      flush_every_n: 1
      fsync_every_n: null
```


The composition root binds the sink adapter based on `sink.kind`.

---

## 10. Testing expectations

Tracing tests must be deterministic and not depend on wall-clock timing beyond non-negativity.

### 10.1 TraceRecorder unit tests

- records contain required fields (step_name, step_index, scenario, trace_id)
- `duration_ms >= 0`
- signature mode respects configuration:
    - in `type_only`, no identity/hash
    - in `hash`, stable value for same message snapshot
- context diff respects whitelist

### 10.2 JsonlTraceSink tests

- emits exactly one JSON object per line
- order is preserved
- supports `flush_every_n`
- batch mode buffers and flushes in chunks
- writing errors are surfaced (or collected) predictably

### 10.3 Runner integration tests (with tracing on)

- ctx.trace has one record per executed step
- external sink was called the same number of times
- error case produces an error record and still progresses according to runner error policy

### 10.4 OTel sink tests (adapter-level)

- does not crash when enabled
- maps step_name to span name
- sets expected attributes
- in tests, use an in-memory exporter (SDK provides test exporters)

---

## 11. Test suite

### 11.1 Record creation and ordering

**Given** scenario of 3 steps and one input message  
**Expect**
- `len(ctx.trace)` equals number of step applications to work items
- `step_index` is 0..2 in order
- all records have same `trace_id`
- entered_at <= exited_at
- duration_ms >= 0

### 11.2 Fan-out produces multiple records in later steps

**Given**
- step1 emits 2 messages
- step2 maps each  
**Expect**
- step1 has 1 record
- step2 has 2 records with work_index 0 and 1
- step2 records appear after step1 record
- msg_after list per record matches mapping

### 11.3 Drop semantics

**Given** 
- step returns empty iterable  
**Expect**
- record exists for that step
- msg_after = []
- no further records for later steps for this input event

### 11.4 Context diff whitelist

**Given**
- whitelist = \["tags"\]
- step mutates `ctx.tags["a"]="1"` and `ctx.metrics["m"]=1`  
**Expect**
- ctx_diff contains only key "tags"
- key "metrics" absent

### 11.5 Replace / add / remove ops

**Given**
- before: tags={"a":"1"}
- after: tags={"a":"2","b":"x"}  
-**Expect**
- ctx_diff["tags"] indicates replace (or you represent “replace entire dict”)
- if using “replace entire value” strategy: op=replace with before/after  
    (keep it simple: replace entire top-level values)

### 11.6 Signature modes

For each signature mode:
- type_only: only type
- type_and_id: includes id_hint when present
- type_and_hash: includes hash  
**Expect** fields present/absent accordingly and stable.

### 11.7 Exception capture

**Given**
- a step that raises  
**Expect**
- record contains `error.type`, `error.message`
- msg_after == []
- runner proceeds according to error policy (separate runner tests)
- trace order remains deterministic

### 11.8 Sink: JSONL writer

With a fake sink capturing written lines:  
**Expect**
- one JSON object per record
- contains required fields
- UTF-8 safe
- order of lines matches ctx.trace order

### 11.9 Truncation policy

**Given** 
- ctx.tags contains a huge value  
**Expect**
- ctx_diff value is truncated to `max_value_len` with a clear indicator (e.g. “…(truncated)”)

---

## 11. Operational notes

- Tracing must be safe to disable at runtime (no-op sink).
- Tracing must not change business behavior (side-effect only).
- JSONL sink is the default for the challenge due to simplicity and grep-friendly output.
- OTel sink is an optional adapter for future integration.

