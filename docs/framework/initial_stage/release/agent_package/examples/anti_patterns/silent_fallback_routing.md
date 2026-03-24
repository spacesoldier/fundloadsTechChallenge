# Anti-Pattern: Silent Fallback Routing in App Bootstrap

## Bad

```python
def route_with_fallback(router, payloads, source):
    try:
        return router.route(payloads, source=source)
    except Exception:
        return []  # silently drop or reroute elsewhere
```

## Why this breaks production behavior

- missing consumer bindings stay hidden
- events are dropped or duplicated without visibility
- debugging depends on guesswork instead of explicit errors

## Good

- keep one declared routing path per message class
- treat missing consumer as explicit failure, or hold-and-replay via dedicated node/service
- surface diagnostics with message class + source node + target binding
