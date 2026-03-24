# Golden Example: File Egress Adapter (Project-Style)

Goal: show how project output is written to file through framework sink path.

## When to use

Use this when your app produces ordered output lines and persists them as `output.txt`-style file data.

## Runtime steps (egress group)

```yaml
control_plane:
  process_groups:
    - name: execution.egress
      workers: 1
      nodes:
        - format_output
        - egress_line_bridge
        - sink:sink
```

`sink:sink` is a framework-provided sink node built around configured `sink` adapter.

## Adapter config (same style as current project)

```yaml
adapters:
  sink:
    settings:
      path: output.txt
      format: text/jsonl
      encoding: utf-8
      flush_every_n: 1
    binds:
      - stream
```

This resolves to the framework file sink adapter (alias of `egress_file`).

## Bridge node in app code

```python
from dataclasses import dataclass
from stream_kernel.adapters.file_io import SinkLine
from stream_kernel.kernel.node import node
from my_app.domain.messages import OutputLine

@node(name="egress_line_bridge", consumes=[OutputLine], emits=[SinkLine])
@dataclass(frozen=True, slots=True)
class EgressLineBridge:
    def __call__(self, msg: OutputLine, _ctx: object | None) -> list[SinkLine]:
        return [SinkLine(text=msg.json_text, seq=msg.line_no)]
```

## Why this design

- app keeps output shaping (`format_output`) in domain/application nodes
- file persistence stays in adapter layer
- output ordering remains deterministic with single egress worker
