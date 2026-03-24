# Anti-Pattern: Off-Graph Bypass in App Code

## Bad (in application service)

```python
class FraudService:
    def __init__(self, transport) -> None:
        self._transport = transport

    def publish(self, payload: dict) -> None:
        self._transport.send("execution.policy", payload)  # bypasses graph
```

## Why this breaks your app

- framework routing/consumer bindings are bypassed
- retries, tracing, and lifecycle signals become inconsistent
- e2e behavior differs from declared scenario/config

## Good

- emit typed message from a node (`return [PolicyInput(...)]`)
- let configured routes and sink nodes deliver it
- keep transport details out of business services
