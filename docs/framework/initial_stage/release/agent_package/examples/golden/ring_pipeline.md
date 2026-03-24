# Golden Example: Ring Pipeline (Application Config)

Goal: define app pipeline so business data goes leaf-to-leaf, while root remains control-plane only.

## Conceptual topology

```text
execution.ingress -> execution.features -> execution.policy -> execution.egress
        \                 \                    \                    \
         +-----------------+--------------------+--------------------> system.observability

root: control only (spawn/start/stop/drain)
```

## What to configure in app

1. Business groups and node order inside each group.
2. Ring data routes between groups (`ingress -> features -> policy -> egress`).
3. Observability routes from every business group to observability group.
4. No business route back through root.

## Source/Sink path in app terms

In most app projects you do **not** write `source:source` and `sink:sink` classes manually.
They are framework-built nodes around configured adapters.

You usually add bridge nodes in app code:

```python
from dataclasses import dataclass
from stream_kernel.adapters.file_io import TextRecord, SinkLine
from stream_kernel.kernel.node import node
from my_app.domain.messages import RawLine, OutputLine

@node(name="ingress_line_bridge", consumes=[TextRecord], emits=[RawLine])
@dataclass(frozen=True, slots=True)
class IngressLineBridge:
    def __call__(self, msg: TextRecord, _ctx: object | None) -> list[RawLine]:
        return [RawLine(line_no=msg.seq or 0, raw_text=msg.text)]

@node(name="egress_line_bridge", consumes=[OutputLine], emits=[SinkLine])
@dataclass(frozen=True, slots=True)
class EgressLineBridge:
    def __call__(self, msg: OutputLine, _ctx: object | None) -> list[SinkLine]:
        return [SinkLine(text=msg.json_text, seq=msg.line_no)]
```

And wire them in group steps with `source:source` at ingress and `sink:sink` at egress.

```yaml
control_plane:
  process_groups:
    - group: execution.ingress
      steps:
        - source:source
        - ingress_line_bridge
        - parse_load_attempt
    - group: execution.egress
      steps:
        - format_output
        - egress_line_bridge
        - sink:sink
```

## Example checklist for agent-generated config review

- `execution.ingress` has source + parsing nodes.
- `execution.egress` has final sink node.
- intermediate groups have only transformation nodes.
- observability group has trace/log/metric dispatch nodes.
- root has control lanes for all groups and only required data relay routes.

## E2E expectations

1. 1,000 input records in -> 1,000 output records out.
2. Output order equals input order.
3. All business groups emit startup-ready and drain-ready.
4. Runtime stops without supervisor timeout.
