# Golden Example: Platform Ports and Injection

Goal: explain how app code should use platform DI ports and adapters.

## Core idea

1. Node injects service (`inject.service(...)`).
2. Service injects a **port contract** (`inject.stream(...)`, `inject.kv(...)`, etc).
3. Adapter implements the port and is selected by config.

This keeps business logic stable while infrastructure changes.

## `inject.*` ports (from framework DI)

- `inject.stream(T)`  
  Stream-like dependency (most common for adapter-backed ports, e.g. `TraceSinkPort`, custom app ports).
- `inject.kv_stream(T)`  
  KV stream transport ports (used for transport-level components).
- `inject.kv(T)`  
  Key-value store port (typed store contracts).
- `inject.request(T)` / `inject.response(T)`  
  Request/response-style channels where used by runtime wiring.
- `inject.service(T)`  
  Inject another `@service` class (or its base contract).
- `inject.queue(T)`  
  Queue-like runtime ports.
- `inject.topic(T)`  
  Topic/pub-sub ports.
- `inject.ipc(T, receive_policy=...)`  
  IPC port access (advanced transport/control-plane cases).

## Why interface ports

- Business node/service sees only contract (`Protocol` / service API).
- Adapter can be swapped via config (`http`, `redis`, `file`, test double).
- App behavior remains deterministic and testable.

## Common framework port interfaces

- `TraceSinkPort` (`stream_kernel.adapters.contracts`)  
  Contract for trace exporters (`emit/flush/close`).
- `BusinessDispatchPort` / `ControlPlaneDispatchPort` (`stream_kernel.adapters.contracts`)  
  Dispatch contracts for boundary/control-plane command paths.
- `QueuePort` / `TopicPort` (`stream_kernel.integration.work_queue`)  
  Queue/topic runtime integration contracts.
- `ExecutionIpcKvStreamPort` (`stream_kernel.execution.transport.ipc.ipc_transport`)  
  IPC transport contract used by IPC adapters.

App code can define additional domain ports the same way (Protocol + adapter bind).

## Example: node + service + port

```python
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.kernel.node import node

@runtime_checkable
class RiskRulesPort(Protocol):
    def score(self, customer_id: str, amount: int) -> int: ...

@service(name="risk_scoring_service")
@dataclass(slots=True)
class RiskScoringService:
    rules: RiskRulesPort = inject.stream(RiskRulesPort)

    def compute(self, customer_id: str, amount: int) -> int:
        return self.rules.score(customer_id, amount)

@node(name="my_app.risk_score", consumes=[dict], emits=[dict])
@dataclass(frozen=True, slots=True)
class RiskScoreNode:
    service: RiskScoringService = inject.service(RiskScoringService)

    def __call__(self, payload: dict, _ctx: object | None) -> list[dict]:
        score = self.service.compute(payload["customer_id"], payload["amount"])
        return [{**payload, "risk_score": score}]
```

## Example: config in node

```python
from dataclasses import dataclass
from stream_kernel.application_context.config_inject import config
from stream_kernel.kernel.node import node

@node(name="my_app.threshold_filter", consumes=[dict], emits=[dict])
@dataclass(frozen=True, slots=True)
class ThresholdFilterNode:
    threshold: int = config.value("limits.threshold", default=1000)

    def __call__(self, payload: dict, _ctx: object | None) -> list[dict]:
        if int(payload["amount"]) < self.threshold:
            return []
        return [payload]
```

Config resolution model:
- first: `nodes.my_app.threshold_filter.limits.threshold`
- fallback: `global.limits.threshold`
