# Anti-Pattern: Blocking I/O on Business Node Path

## Bad

```python
@node(name="my_app.enrich", consumes=[InboundRecord], emits=[EnrichedRecord])
def enrich(payload, _ctx):
    requests.post("https://slow-service", json={"id": payload.record_id}, timeout=10)
    return [EnrichedRecord(...)]
```

## Why this hurts

- hot-path throughput collapses
- queue pressure grows across groups
- shutdown/drain timing becomes unstable

## Good

- keep node path CPU-light and deterministic
- move external I/O to dedicated adapter/worker path
- if external call is mandatory, isolate it with explicit bounded strategy and observability
