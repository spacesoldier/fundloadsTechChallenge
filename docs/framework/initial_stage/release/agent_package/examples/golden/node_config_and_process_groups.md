# Golden Example: Node Config + Process Group Split

Goal: show two practical things:
- how node fields are configured from YAML
- how nodes are split into multiple processes via config

## 1) Node configured from YAML

`src/my_app/runtime/nodes.py`
```python
from dataclasses import dataclass
from stream_kernel.application_context.config_inject import config
from stream_kernel.kernel.node import node
from my_app.domain.messages import InboundRecord, EnrichedRecord

@node(name="my_app.enrich_record", consumes=[InboundRecord], emits=[EnrichedRecord])
@dataclass(frozen=True, slots=True)
class EnrichRecordNode:
    high_threshold: int = config.value("risk.high_threshold", default=10_000)
    vip_multiplier: float = config.value("risk.vip_multiplier", default=1.5)

    def __call__(self, payload: InboundRecord, _ctx: object | None) -> list[EnrichedRecord]:
        # simplified example
        return [EnrichedRecord(payload.record_id, "retail", "high" if payload.amount >= self.high_threshold else "normal")]
```

YAML (node-specific + global fallback):

```yaml
global:
  risk:
    high_threshold: 9000
    vip_multiplier: 2.0

nodes:
  my_app.enrich_record:
    risk:
      high_threshold: 12000
```

Resolution rule:
- first: `nodes.my_app.enrich_record.risk.high_threshold`
- fallback: `global.risk.high_threshold`
- then default from `config.value(..., default=...)`

## 2) Split nodes into processes

In this project style, process split is defined in `runtime.platform.process_groups`.

```yaml
runtime:
  platform:
    process_groups:
      - name: execution.ingress
        workers: 1
        nodes:
          - source:source
          - ingress_line_bridge
          - parse_load_attempt

      - name: execution.features
        workers: 2
        nodes:
          - compute_time_keys
          - my_app.enrich_record

      - name: execution.policy
        workers: 2
        nodes:
          - evaluate_policies
          - update_windows

      - name: execution.egress
        workers: 1
        nodes:
          - format_output
          - egress_line_bridge
          - sink:sink

      - name: system.observability
        workers: 1
        nodes:
          - system.obs.trace_dispatch
          - system.obs.log_dispatch
          - system.obs.monitor_dispatch
```

## Practical checks

1. Every node name in `process_groups[*].nodes` must exist in discovered nodes/system nodes.
2. Keep ingress/egress with `workers: 1` unless you explicitly handle ordering/partitioning.
3. Start with one worker per middle group, then scale `workers` and re-run e2e determinism checks.
