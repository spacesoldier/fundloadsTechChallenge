# Golden Example: Lifecycle Chain (App Perspective)

Goal: understand where app logic plugs into framework lifecycle.

## What framework handles

- process spawn and control-plane startup
- discovery/config apply stages
- start-work and stop coordination
- drain-ready aggregation

## What app provides

- business nodes
- business message bindings
- project adapters/config

## App-safe lifecycle contract

```text
framework startup
  -> app nodes initialized
  -> start-work signal
  -> app business processing
  -> app emits terminal business marker (tombstone/EOF-driven completion path)
  -> framework emits drain-ready
  -> framework coordinated stop
```

## Minimal app checks (must-have tests)

1. Startup test: app nodes are reachable after config apply.
2. Processing test: input records are transformed in expected order.
3. Completion test: completion marker reaches final stage and drain-ready is emitted once.
4. Shutdown test: runtime exits without timeout.

## Practical rule

Do not implement custom off-graph lifecycle control in app services. If lifecycle behavior is needed, model it as messages and nodes.
