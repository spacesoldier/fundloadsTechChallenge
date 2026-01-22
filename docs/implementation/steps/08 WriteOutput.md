# Step Spec 

**Step name (registry):** `write_output`  
**File:** `docs/implementation/steps/08 WriteOutput.md`  
**Responsibility:** Persist the stream of `OutputLine` objects into `output.txt` exactly in input order, as required by the challenge.

This step performs I/O and is the final sink of the pipeline.

---

## 1. Contract

### Input
- `OutputLine`
  - `line_no: int`
  - `json_text: str`

### Output
- None (sink step)
  - for pipeline uniformity it may emit the same `OutputLine`, but default is “no outputs”.

### Signature
`(msg: OutputLine, ctx: Ctx) -> Iterable[OutputLine] | None`

**Cardinality:** sink (0 outputs) OR pass-through (1 output).  
For simplicity, treat as a sink and return nothing.

---

## 2. Configuration dependencies

From `docs/Configuration Spec.md`:

```yaml
output:
  file_path: "output.txt"
  mode: "ndjson"          # ndjson is line-delimited JSON objects
  fsync: false            # optional
  atomic_replace: true    # write to temp then rename
```

Recommended for the challenge:
- `mode: ndjson`
- `atomic_replace: true`

---

## 3. Output file format

### 3.1 Line-delimited JSON (NDJSON)

Write **one JSON object per line**, in stream order.

Example:
```json
{"id":"15887","customer_id":"528","accepted":true} {"id":"15888","customer_id":"528","accepted":false}
```

No surrounding array, no commas.

(If the task explicitly expects an array, we can switch modes, but default here is NDJSON as the safest “stream-friendly” interpretation.)

---

## 4. Ordering guarantees

### 4.1 Default (single-threaded)

If the pipeline is sequential:
- write in arrival order.

### 4.2 Defensive ordering (future-proof)

If concurrency is added later, preserve ordering by:
- buffering out-of-order lines keyed by `line_no`,
- writing when `next_expected_line_no` is available.

For this challenge we can implement the simple mode, but the spec supports the robust mode.

**Invariant:** output lines must be written in strictly increasing `line_no`.

---

## 5. Atomic write strategy

To avoid partially-written outputs:
1. write to a temp file (e.g. `output.txt.tmp`)
2. flush/close
3. rename to `output.txt` (atomic on most filesystems)

Optional:
- `fsync` temp file before rename if `output.fsync=true`.

---

## 6. Error handling semantics

If file write fails:
- raise an exception and fail the run (challenge environment expects correctness over partial results).

No retries in the default implementation.

---

## 7. Invariants (INV-*)

### INV-01: Order preservation

Lines in `output.txt` appear in strictly increasing `line_no`.

### INV-02: Exactly one line per input event (post-idempotency classification)

Every input line produces exactly one output line (because earlier steps always emit a Decision; duplicates become declined decisions).

### INV-03: File contains valid NDJSON

Each line is valid JSON and matches the schema.

### INV-04: No partial output file on success

With atomic_replace enabled, the final output file is complete.

---

## 8. Edge cases to cover (tests)

1. Small stream of 3 lines writes exactly 3 lines
2. Ensure trailing newline behavior is consistent:
    - recommended: final newline at end of file (POSIX-friendly), but not strictly required if evaluator is robust
3. Robust ordering mode (optional):
    - deliver lines out of order, confirm written order is correct
4. Atomic replace:
    - ensure temp file is replaced and final file exists

---

## 9. Diagnostics and context usage

Optional:
- `ctx.metrics["output.written_lines"] += 1`
- log final path and line count at end of run

Ctx is not required for correctness.

---

## 10. Test plan (before code)

### Unit tests (pytest)

Use a temp directory.

Suggested tests:
- `test_write_output_ndjson_exact_lines()`
- `test_write_output_preserves_order()` (if implementing ordering buffer)
- `test_write_output_atomic_replace()`

Assertions:
- file exists at configured path
- number of lines equals number of inputs
- lines match expected text exactly
- ordering by `line_no` is correct

---

## 11. Non-goals

- No compression.
- No JSON pretty printing.
- No batching beyond simple buffered writes.
- No uploading / external sinks.