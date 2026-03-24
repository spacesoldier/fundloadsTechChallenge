# Anti-Pattern: Global Mutable State in App Runtime

## Bad

```python
SEEN_RECORD_IDS: set[str] = set()
LAST_WINDOW_END = None
```

Used from nodes/services directly.

## Why this breaks your app

- state leaks between tests/runs
- multiprocessing behavior becomes non-deterministic
- impossible to reason about recovery/drain semantics

## Good

- keep state in injected store abstraction
- expose mutations via service API
- node reads payload, delegates state updates to service/store
