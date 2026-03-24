# Golden Example: File Source Adapter (Project-Style)

Goal: show the same pattern used in this project: file input through `source:source`.

## When to use

Use this when your app reads ordered records from a file and must preserve line order.

## Runtime steps (ingress group)

```yaml
control_plane:
  process_groups:
    - name: execution.ingress
      workers: 1
      nodes:
        - source:source
        - ingress_line_bridge
        - parse_load_attempt
```

`source:source` is a framework-provided source node built around the configured `source` adapter.

## Adapter config (same style as current project)

```yaml
adapters:
  source:
    emit_tombstone: true
    settings:
      path: input.txt
      format: text/jsonl
      encoding: utf-8
      decode_errors: strict
    binds:
      - stream
```

This resolves to the framework file source adapter (alias of `ingress_file`).

## Bridge node in app code

```python
from dataclasses import dataclass
from stream_kernel.adapters.file_io import TextRecord
from stream_kernel.kernel.node import node
from my_app.domain.messages import RawLine

@node(name="ingress_line_bridge", consumes=[TextRecord], emits=[RawLine])
@dataclass(frozen=True, slots=True)
class IngressLineBridge:
    def __call__(self, msg: TextRecord, _ctx: object | None) -> list[RawLine]:
        if msg.seq is None:
            raise ValueError("TextRecord.seq is required for deterministic ordering")
        return [RawLine(line_no=msg.seq, raw_text=msg.text)]
```

## Why this design

- app keeps domain parsing/validation in app nodes
- transport/file details stay in adapter layer
- `emit_tombstone: true` gives deterministic end-of-stream signal for shutdown chain
