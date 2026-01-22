# Step Spec

**Step name (registry):** `format_output`  
**File:** `docs/steps/07 FormatOutput.md`  
**Responsibility:** Convert an internal `Decision` into the exact JSON object required by the challenge output format, without performing any file I/O.

This step is pure and deterministic.

---

## 1. Contract

### Input
- `Decision`
  - `line_no: int`
  - `id: str`
  - `customer_id: str`
  - `accepted: bool`
  - (other internal fields exist but are ignored for output)

### Output
- `OutputLine`
  - `line_no: int` (used only to preserve order downstream)
  - `json_text: str` (one JSON object, no trailing comma)
  - optionally: `json_obj: dict` (for tests; not required in production)

### Signature
`(msg: Decision, ctx: Ctx) -> Iterable[OutputLine]`

**Cardinality:** exactly 1 output per input.

---

## 2. Output schema (challenge)

Each output record must be a valid JSON object with exactly these fields:

```json
{
  "id": "15887",
  "customer_id": "528",
  "accepted": true
}
```

Rules:

- `id` and `customer_id` MUST be strings (even if numeric).
- `accepted` MUST be a JSON boolean (`true`/`false`).
- No extra fields (unless explicitly allowed by the task — assume NOT allowed).

---

## 3. Field mapping

- `id` ← `Decision.id`
- `customer_id` ← `Decision.customer_id`
- `accepted` ← `Decision.accepted`

Everything else is ignored.

---

## 4. JSON serialization requirements

### 4.1 Deterministic key order

To avoid noisy diffs and to keep tests stable:
- Serialize keys in a fixed order: `id`, `customer_id`, `accepted`.

Python note:
- most json libs preserve insertion order; ensure construction follows desired order.

### 4.2 Whitespace

Either is acceptable as long as JSON is valid, but recommend compact JSON:
- no spaces after separators (e.g. `separators=(",", ":")`)

Example:  
`{"id":"15887","customer_id":"528","accepted":true}`

### 4.3 Newlines

This step does not add newlines. Newline handling is responsibility of Step 08.

---

## 5. Ordering preservation

Include `line_no` in `OutputLine` so that Step 08 can write in original order.

This is especially important if later we introduce concurrency. For now, the pipeline is sequential, but we still keep the invariant explicit.

---

## 6. Invariants (INV-*)

### INV-01: Exactly one output per input

Always emits one `OutputLine`.

### INV-02: Output JSON is valid

`json_text` must parse as JSON and match the schema types.

### INV-03: Only required keys are present

No extra fields are emitted.

### INV-04: Deterministic serialization

Given the same input Decision, output text is identical.

---

## 7. Edge cases to cover (tests)

1. approved decision → accepted=true in JSON
2. declined decision → accepted=false in JSON
3. id/customer_id contain only digits but must remain strings
4. Ensure no reasons leak into output
5. Deterministic key order

---

## 8. Diagnostics and context usage

Optional:
- `ctx.metrics["output.formatted"] += 1`

Ctx is not required for correctness.

---

## 9. Test plan (before code)

### Unit tests (pytest)

- `test_format_output_schema_and_types()`
- `test_format_output_deterministic_key_order()`
- `test_format_output_no_extra_fields()`

Assertions:
- output cardinality = 1
- `json.loads(json_text)` returns dict with exactly 3 keys
- key order in text matches expected (string comparison)
- types: id/customer_id are str, accepted is bool

---

## 10. Non-goals

- No file writing.
- No buffering.
- No pretty printing.
- No aggregation across lines.