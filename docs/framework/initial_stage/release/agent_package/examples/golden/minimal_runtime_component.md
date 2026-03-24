# Golden Example: Minimal Runtime Component (Node -> Service -> Adapter Port)

Goal: show full app-style dependency chain:
- `@node` injects `@service`
- `@service` injects a **port interface**
- adapter implementation behind the port can be swapped without changing business node logic

## Poetry dependency

```toml
[tool.poetry.dependencies]
python = "^3.13"
ringo = "^0.0.1" # package name; import path is stream_kernel
```

## App structure

```text
src/my_app/
  domain/messages.py
  domain/ports.py
  domain/services.py
  runtime/nodes.py
```

## App code

`src/my_app/domain/messages.py`
```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class InboundRecord:
    record_id: str
    customer_id: str
    amount: int

@dataclass(frozen=True, slots=True)
class EnrichedRecord:
    record_id: str
    customer_segment: str
    risk_bucket: str
```

`src/my_app/domain/ports.py`
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class CustomerProfilePort(Protocol):
    def segment_of(self, customer_id: str) -> str: ...
```

`src/my_app/domain/services.py`
```python
from dataclasses import dataclass
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from my_app.domain.ports import CustomerProfilePort

@service(name="record_enrichment_service")
@dataclass(slots=True)
class RecordEnrichmentService:
    profiles: CustomerProfilePort = inject.stream(CustomerProfilePort)

    def enrich(self, *, customer_id: str, amount: int) -> tuple[str, str]:
        segment = self.profiles.segment_of(customer_id)
        risk_bucket = "high" if amount >= 10_000 else "normal"
        return segment, risk_bucket
```

`src/my_app/runtime/nodes.py`
```python
from dataclasses import dataclass
from stream_kernel.application_context.config_inject import config
from stream_kernel.application_context.inject import inject
from stream_kernel.kernel.node import node
from my_app.domain.messages import InboundRecord, EnrichedRecord
from my_app.domain.services import RecordEnrichmentService

@node(name="my_app.enrich_record", consumes=[InboundRecord], emits=[EnrichedRecord])
@dataclass(frozen=True, slots=True)
class EnrichRecordNode:
    enrichment: RecordEnrichmentService = inject.service(RecordEnrichmentService)
    high_risk_threshold: int = config.value("risk.high_threshold", default=10_000)

    def __call__(self, payload: InboundRecord, _ctx: object | None) -> list[EnrichedRecord]:
        segment, risk_bucket = self.enrichment.enrich(
            customer_id=payload.customer_id,
            amount=payload.amount,
        )
        if payload.amount >= self.high_risk_threshold:
            risk_bucket = "high"
        return [EnrichedRecord(payload.record_id, segment, risk_bucket)]
```

## Notes

- `config.value(...)` resolves from `nodes.<node_name>.*` with fallback to `global.*`.
- Node logic stays stable even if adapter implementation for `CustomerProfilePort` changes.
