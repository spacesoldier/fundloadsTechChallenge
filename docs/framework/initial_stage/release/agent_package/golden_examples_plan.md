# Golden Examples Plan

This file defines the minimal canonical examples required for agent onboarding.

## Required Golden Examples

1. Minimal runtime component:
- one node
- one service
- one store
- one deterministic test

2. Minimal extension/plugin:
- adapter registration
- adapter settings validation
- adapter e2e smoke test

3. Correct lifecycle chain:
- init event
- discovery/config path
- start-work path
- shutdown/drain path

4. Ring data-plane example:
- multi-leaf pipeline
- direct leaf-to-leaf business data flow
- observability leaf ingestion path

## Required Anti-Patterns

1. Off-graph direct service call that bypasses node routing.
2. Global mutable state for lifecycle decisions.
3. Blocking operation in async node path.
4. Fallback routing that changes semantics silently.

Each anti-pattern example must include:

1. broken code snippet
2. why it is wrong
3. corrected code snippet
4. test that catches regression

## Suggested Layout

```text
examples/
  golden/
    minimal_component/
    minimal_adapter/
    lifecycle_chain/
    ring_pipeline/
  anti_patterns/
    off_graph_bypass/
    global_state/
    blocking_io/
    silent_fallback/
```

