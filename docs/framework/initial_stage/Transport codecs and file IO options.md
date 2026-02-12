# Transport codecs and file IO options

This document defines the target support matrix for transport codecs and file IO
strategies in the framework. It is intentionally framework-level and does not
depend on challenge-specific payload shapes.

It complements:

- [File adapters and ordering model](File%20adapters%20and%20ordering%20model.md)
- [Ports and adapters model](Ports%20and%20adapters%20model.md)
- [Router and DAG roadmap](Router%20and%20DAG%20roadmap.md)

---

## 1) Scope and principles

- Business nodes remain codec-agnostic.
- Serialization/deserialization is a transport boundary concern.
- Codec/backend selection is config-driven.
- Error policy is deterministic and explicit (no silent fallback by default).
- Ordering (`seq`) remains transport metadata and is preserved independently of codec.

---

## 2) Codec support catalog (target)

### 2.1 JSON family

- `json` (stdlib)
- `orjson`
- `msgspec` (JSON mode)
- `ujson` (optional compatibility backend)

### 2.2 Binary payload codecs

- `msgpack`
- `cbor2`

### 2.3 Schema-based codecs

- `protobuf`
- `avro` / `fastavro`
- `thrift` (optional)
- `flatbuffers` (optional)

### 2.4 Validation/transformation layers

- `pydantic` (schema validation and conversion)
- `msgspec` (typed decoding/encoding)
- `marshmallow` (schema-driven transformation)

---

## 3) File IO support catalog (target)

### 3.1 Baseline file access

- `pathlib/open` text mode with explicit `encoding`
- `pathlib/open` binary mode
- atomic replace write mode

### 3.2 Streaming and large-file strategies

- line-by-line text streaming (`TextRecord`)
- chunked binary streaming (`ByteRecord`)
- memory-mapped read (`mmap`) for large files
- streaming parse for large JSON (`ijson`) as optional plugin path

### 3.3 Compression and storage backends

- `gzip`, `bz2`, `lzma`
- `zstandard` (optional)
- remote file systems via `fsspec` (S3/GCS/etc.) as optional backend

### 3.4 Columnar/data formats (optional track)

- `parquet` (`pyarrow`)
- CSV/TSV adapters

---

## 4) Configuration model (target)

Codec and file backend are selected from adapter settings.

Example intent (shape only):

- `settings.format`: transport media hint (`text/jsonl`, `application/octet-stream`, ...)
- `settings.codec`: codec id (`orjson`, `msgpack`, `protobuf`, ...)
- `settings.encoding`: text encoding when applicable
- `settings.decode_errors`: decode policy for text paths
- `settings.backend`: IO backend (`local`, `fsspec`, ...)

Exact validator rules are introduced incrementally via TDD.

---

## 5) Error-policy contract (target)

Supported deterministic policies:

- `strict` -> fail fast
- `replace` -> replacement-char strategy for text decoding only
- `drop` -> explicit drop with observability event
- `dlq` -> send failed payload to dead-letter sink (future stage)
- `retry` -> bounded retry policy (future stage)

---

## 6) Performance guidance

- Default minimal path for current demo project:
  - ingress: `text/jsonl` + `utf-8` + `strict`
  - egress: `text/jsonl` + `utf-8`
- High-throughput path candidates:
  - JSON: `orjson` or `msgspec`
  - schema-based: `protobuf` / `fastavro`
- Keep Pydantic for validation boundaries where correctness is critical; avoid
  forcing full-model validation on every hot-path hop.

---

## 7) Test-case matrix (to be implemented)

`CODEC-01` codec selection from config resolves deterministic adapter behavior.

`CODEC-02` unsupported codec id fails config validation.

`CODEC-03` same payload round-trips encode/decode for each supported codec.

`CODEC-04` decode error policy (`strict`/`replace`) behaves deterministically.

`FILE-01` text file ingress preserves ordering and emits `TextRecord(seq=...)`.

`FILE-02` binary ingress preserves raw bytes and emits `ByteRecord(seq=...)`.

`FILE-03` sink writes with configured `encoding`.

`FILE-04` sink rejects incompatible format/codec combinations.

`FILE-05` large-file strategy keeps bounded memory (streaming path).

`FILE-06` ordered sink mode (`source_seq`) preserves input order under parallel execution.

