# File adapters and ordering model

This document specifies framework-level file ingestion/egress behavior so file IO
stays transport-focused and does not leak project/domain semantics.

It complements:

- [Ports and adapters model](Ports%20and%20adapters%20model.md)
- [Routing semantics](Routing%20semantics.md)
- [Execution planning model](Execution%20planning%20model.md)

---

## 1) Problem statement

Files may contain:

- text records (`jsonl`, `plain text`, CSV-like lines),
- binary records,
- mixed transport payloads where parsing belongs to project nodes, not adapters.

Framework file adapters must therefore:

- avoid domain-specific payload shapes,
- preserve deterministic input order,
- support future parallel processing while keeping output order recoverable.

---

## 2) Runtime model

### 2.1 Content format hint (config)

File adapters use a transport hint analogous to `content-type`:

- `text/jsonl`
- `text/plain`
- `application/octet-stream`

The hint is adapter-level metadata (settings), not domain model metadata.

### 2.2 Framework payload models

Framework should route typed transport models, not naked `str`/`bytes`:

- `TextRecord(text: str, seq: int | None, source: str, encoding: str = "utf-8")`
- `ByteRecord(payload: bytes, seq: int | None, source: str)`

Current `StreamLine` can be treated as transitional equivalent of `ByteRecord`.

### 2.4 Stage A/B/C implemented status

Current code path (implemented):

- framework ingress supports `settings.format`:
  - `text/jsonl` -> emits `TextRecord`
  - `text/plain` -> emits `TextRecord`
  - `application/octet-stream` -> emits `ByteRecord`
- default ingress format is `text/jsonl`
- ingress decode policy is adapter-level:
  - `decode_errors=strict` -> fail fast on invalid bytes
  - `decode_errors=replace` -> replacement-char fallback (`U+FFFD`)
- current project ingress bridge accepts `TextRecord` only and converts to `RawLine`
- framework egress supports text sink settings:
  - `settings.format`: `text/jsonl` (default) or `text/plain`
  - `settings.encoding`: output encoding (default `utf-8`)
- backward compatibility alias `StreamLine = ByteRecord` is preserved during migration

Implementation references:

- `src/stream_kernel/adapters/file_io.py`
- `src/fund_load/usecases/steps/io_bridge.py`
- `src/stream_kernel/config/validator.py`

Test references (Stage A):

- `tests/adapters/test_input_source.py`
- `tests/stream_kernel/adapters/test_file_io.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/usecases/steps/test_io_bridge.py`
- `tests/usecases/steps/test_step_node_contracts.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`

### 2.3 Sequence semantics

`seq` is a transport ordinal assigned by ingress adapter.

- It is not a domain line number.
- It exists to preserve/reconstruct order across asynchronous execution.
- Domain mapping (`RawLine.line_no`, etc.) happens in project bridge nodes.

Runtime sink ordering mode:

- `runtime.ordering.sink_mode=completion`:
  - sink observes queue/completion order (current baseline behavior)
- `runtime.ordering.sink_mode=source_seq`:
  - sink delivery requires `__seq` in trace context
  - runner fails fast if sink receives payload without `__seq`

`SinkLine` also carries optional `seq` so sink-side adapters/reorder stages can
consume explicit transport order metadata when needed.

---

## 3) Read -> process -> write pattern

### 3.1 Recommended baseline (deterministic)

1. Single reader ingress adapter reads source sequentially.
2. Emits transport records with monotonic `seq`.
3. Router fans out to processing nodes/workers.
4. Sink/reorder stage commits outputs in `seq` order.

This keeps deterministic behavior with minimal complexity.

### 3.2 Parallel processing with ordered commit

When node processing is parallel:

- completion order may differ from input order;
- sink path must use a reorder buffer:
  - maintain `next_expected_seq`,
  - temporarily store out-of-order records,
  - flush contiguous sequence on arrival.

### 3.3 Reading one file in many readers

Possible but not default.

Requires:

- byte-range partitioning,
- newline-boundary alignment for text formats,
- global merge by `seq`.

Given complexity, framework default remains single-reader ingress + downstream parallelism.

---

## 4) Adapter responsibilities

File adapters are transport-only:

- decode/encode at transport boundary (`bytes <-> text` if configured),
- assign `seq`,
- emit/consume framework transport records,
- never produce domain models directly.

Domain conversion belongs to bridge nodes (project side), for example:

- `TextRecord -> RawLine`
- `OutputLine -> SinkRecord`

---

## 5) Routing and observability implications

- Router routes by transport model type.
- Tracing/telemetry can include `seq` as stable ordering metadata.
- No dependency on project-specific fields (for example `line_no`) at framework level.

---

## 6) Test cases (to keep architecture stable)

1. Ingress emits transport records with monotonically increasing `seq`.
2. Text ingress preserves blank lines as empty payloads when format is line-oriented.
3. Binary ingress preserves raw bytes without text assumptions.
4. Bridge node maps transport record to domain record deterministically.
5. Parallel processing + reorder buffer writes in original `seq` order.
6. Missing `seq` in `source_seq` sink mode fails fast at sink stage.
7. `completion` sink mode keeps completion/FIFO order (characterization).
8. Tracing includes transport-level `seq` but not domain-specific `line_no`.
9. Egress sink validates text-only formats and honors configured output encoding.

Implemented status:

- 1, 2, 3, 4 are covered by current tests.
- decode strict/fallback behavior is covered in bridge tests.
- 5 remains execution-stage follow-up (reorder buffer engine is not implemented yet).
- 6 is covered by runner strict sink guard tests (`source_seq` mode).
- 7 is covered by runner completion-order characterization tests.
- 8 is partially covered (transport `seq` path), expanded observability assertions planned.
- 9 is covered by output sink adapter tests.

Test references:

- `tests/stream_kernel/adapters/test_file_io.py`
- `tests/stream_kernel/config/test_newgen_validator.py`
- `tests/usecases/steps/test_io_bridge.py`
- `tests/adapters/test_output_sink.py`
- `tests/stream_kernel/execution/test_context_service.py`
- `tests/stream_kernel/execution/test_runner_context_integration.py`
- `tests/stream_kernel/app/test_framework_run.py`
- `tests/integration/test_end_to_end_baseline_limits.py`
- `tests/integration/test_end_to_end_experiment_features.py`

---

## 7) Forward path to HTTP API

The same model maps directly to network interfaces:

- request body chunks -> `ByteRecord`/`TextRecord`,
- streaming responses commit by `seq` when order matters,
- protocol-specific parsing remains adapter concern.

This enables adding FastAPI/WebSocket/HTTP2 adapters without changing node contracts.
