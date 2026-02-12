# Codec and file IO expansion plan

Goal: implement framework-owned, config-driven codec/file-IO support without
leaking transport details into business nodes.

Primary references:

- [Transport codecs and file IO options](../Transport%20codecs%20and%20file%20IO%20options.md)
- [File adapters and ordering model](../File%20adapters%20and%20ordering%20model.md)
- [Router and DAG roadmap](../Router%20and%20DAG%20roadmap.md)

---

## Stage 0 — Contract freeze (docs + tests first)

- [ ] Freeze config keys: `format`, `codec`, `encoding`, `decode_errors`, `backend`.
- [ ] Add validator characterization tests for currently implemented subset.
- [ ] Add negative tests for unsupported codec/backend combinations.

## Stage 1 — JSON baseline codecs

- [ ] Add codec abstraction contract (encode/decode interface).
- [ ] Implement `json` baseline adapter.
- [ ] Add optional `orjson` adapter path with fallback handling.
- [ ] Add roundtrip + error-policy tests.

## Stage 2 — Binary + schema codecs

- [ ] Add `msgpack` adapter.
- [ ] Add `protobuf` adapter contract + sample schema tests.
- [ ] Add `avro/fastavro` adapter contract + schema evolution baseline tests.

## Stage 3 — File backend extensions

- [ ] Add compressed file adapter variants (`gzip` baseline first).
- [ ] Add optional `fsspec`-based remote backend contract.
- [ ] Add backend conformance tests (same transport contract, different backend).

## Stage 4 — Ordering and high-volume output

- [ ] Add reorder buffer for `source_seq` sink mode under concurrent processing.
- [ ] Add spill-to-disk/chunk merge strategy tests for very large runs.
- [ ] Keep deterministic final output order validation.

## Stage 5 — Observability and failure handling

- [ ] Emit structured observability events on codec failures.
- [ ] Add deterministic failure policies (`strict/drop/retry/dlq` staged rollout).
- [ ] Add integration tests for failure routing and diagnostics.

## Stage 6 — Project migration templates

- [ ] Provide minimal config presets:
  - `text/jsonl + utf-8 + strict` (default)
  - `json + orjson` (fast JSON)
  - `protobuf` (schema-based)
- [ ] Add migration notes for project teams replacing custom codec code.

