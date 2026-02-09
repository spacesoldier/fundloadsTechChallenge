# Intro

This document defines the **InputSource boundary**: how raw load-attempt events enter the system.

The core principles:
- the engine processes an **ordered stream** of events
- input is an **adapter concern**
- the pipeline must not care whether events come from a file, queue, HTTP, etc.

For the challenge, we provide one adapter:
- **NDJSON file reader** (one JSON object per line)

But the contract is designed to allow future sources.

---

**Framework adapter contract:** [file_io.py](../../../src/stream_kernel/adapters/file_io.py)  
**Project mapping (NDJSON -> RawLine):** [io.py](../../../src/fund_load/adapters/io.py)

---

## 1. Purpose and scope

InputSource is responsible for:
- producing an ordered stream of raw input records,
- attaching minimal ingestion metadata (e.g. `line_no`),
- handling I/O errors deterministically.

InputSource is **not** responsible for:
- parsing domain objects (that is Step 01),
- applying policies,
- performing idempotency,
- managing window state.

---

## 2. Stream contract

### 2.1 Ordering
The engine assumes:
- records are processed **in the order yielded by InputSource**
- for file input, that is physical line order

This ordering is critical because window updates must follow the same sequence.

### 2.2 One record = one raw event
Each yielded item represents exactly one raw attempt.

For NDJSON, that is one line of text containing one JSON object.

---

## 3. Port interface

### 3.1 Raw record structure

The core does not require full parsing at this stage.
A raw record must carry:

- `line_no: int` (1-based, monotonically increasing)
- `raw_line: str` (original line string, without trailing newline)

Optionally:
- `source_id: str` (file name, topic name, etc.)
- `received_at: datetime` (ingestion timestamp)

### 3.2 InputSource contract

Conceptually:

- `InputSource.read() -> Iterable[RawRecord]`

Where `RawRecord` is an immutable value object.

Notes:
- InputSource may be implemented as a generator.
- The engine should treat it as a single-pass iterator.

---

## 4. NDJSON File Input adapter (challenge implementation)

### 4.1 Supported format
- UTF-8 text file
- each non-empty line is expected to be a JSON object
- blank lines may be skipped (define policy; recommended: skip)

Example line:

`{"id":"15887","customer_id":"528","load_amount":"$3318.47","time":"2000-01-01T00:00:00Z"}`

### 4.2 Line numbering
- `line_no` is assigned by the adapter during reading.
- Start at 1.
- Increment for every physical line read (including invalid lines) **or** only for yielded records.

Recommended (for auditability and reproducibility):
- assign `line_no` by physical line position (including invalid lines),
- but still yield invalid lines as records so parsing can emit a deterministic error reason.

For the challenge dataset, lines are expected to be valid events.  
If we decide to skip blank lines, document it explicitly.

### 4.3 Error handling

File-level errors (cannot open/read):
- fail fast (terminate run with clear message)

Line-level errors:
- do not crash in the adapter
- yield the raw line with its `line_no`
- parsing step decides whether this becomes:
  - a declined output line, or
  - a hard failure (depending on chosen mode)

Recommended for this challenge:
- treat malformed JSON as a hard failure (simpler, safer),
- because the required output is a fully valid JSON stream.

(If you want a “robust mode”, make it configurable.)

---

## 5. Two-pass input and pre-scan (optional)

Some features may benefit from a pre-scan step:
- prime range selection (min/max id)
- dataset profiling

If we use a two-pass approach for the final solution:
- the first pass reads and stores minimal extracted data (or rewinds the file if possible)
- the second pass performs the real adjudication

For strict streaming integrations (Kafka, etc.), rewinding is not possible.
In that case:
- prime check must support dynamic or cached on-the-fly evaluation.

We keep this as an implementation option, not a requirement of the port.

---

## 6. Test strategy

### 6.1 Unit tests for FileInputSource
- reads N lines → yields N records with correct line_no
- preserves exact raw_line content (excluding newline)
- handles blank lines according to policy (skip or yield)

### 6.2 Error tests
- missing file path → fails fast with readable exception
- file permission denied → fails fast

### 6.3 Determinism tests
- the same input file produces identical `(line_no, raw_line)` sequence
- independent of environment

---

## 7. Extensibility examples (future)

The same port supports:
- Kafka topic source:
  - `line_no` becomes `offset`
- HTTP ingestion:
  - `line_no` becomes `request_seq`
- S3 object streaming:
  - `line_no` remains line-based

Core pipeline remains unchanged.

---
