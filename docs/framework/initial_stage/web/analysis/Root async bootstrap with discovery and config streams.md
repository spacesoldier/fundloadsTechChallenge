# Root async bootstrap with discovery and config streams

## Purpose

Lock a platform-rail startup model where:

- `app.run()` always starts the **root** process in async runner mode;
- bootstrap orchestration is message-driven (`@node -> @service -> @adapter`);
- startup is gated by a **config stream completion barrier**;
- no business execution starts until discovery + config stream are fully processed.

Related:

- [Control-plane bootstrap root/leaf sequences](Control-plane%20bootstrap%20root-leaf%20sequences.md)
- [Platform discovery runtime stream scenario](Platform%20discovery%20runtime%20stream%20scenario.md)
- [Supervisor control-plane architecture](Supervisor%20control-plane%20architecture.md)
- [IPC transport and worker lifecycle services](IPC%20transport%20and%20worker%20lifecycle%20services.md)

## Core assumptions

1. Root `app.run()` uses async runner unconditionally.
2. Sync/async auto-selection is not used for root execution path.
3. Startup orchestration is not hidden in `bootstrap.py`-style hardcode.
4. Services do not own transport/storage details directly; they use ports.
5. Adapters implement concrete backends (filesystem now, DB/registry later).

## Bootstrap kernel (minimal hardcode)

A tiny static kernel is allowed before DI graph is fully active:

- enqueue initial pulse (`ControlPlaneRootPulse` or `ControlPlaneLeafPulse`);
- register minimal startup system nodes required to consume that pulse;
- start runner loop.

Everything after that pulse is handled by platform nodes/services/adapters.

## Root startup sequence

1. `app.run()` builds runtime artifacts and enqueues `ControlPlaneRootPulse`.
2. Async runner starts.
3. `system.cp.root_bootstrap` calls bootstrap service.
4. Bootstrap service requests discovery adapter:
   - emits discovery items as stream events;
   - emits discovery completed event.
5. Config stream bootstrap node starts config ingestion:
   - config adapter emits typed config records from YAML (current backend);
   - emits config completed event.
6. Startup barrier node waits for both:
   - `discovery.completed`;
   - `config.completed`.
7. Only after barrier opens:
   - launch plan node runs;
   - lifecycle spawn dispatch starts workers;
   - business/source ingress flow is allowed.

## Leaf startup sequence

1. Leaf process starts and enqueues `ControlPlaneLeafPulse`.
2. Leaf bootstrap node sends `leaf_hello`.
3. Root sends `leaf_config_card`.
4. Leaf applies config card, performs subset discovery through same discovery service+adapter contract.
5. Leaf emits `leaf_config_ack`.
6. Root updates runtime state and advances lifecycle stage.

## Discovery architecture (`node -> service -> adapter`)

### Node

- Initiates discovery in response to pulse.
- Does not scan files or parse modules directly.

### Service

- Coordinates discovery flow and emits typed events.
- Persists progress through injected state/KV ports.

### Adapter

- Backend implementation:
  - now: Python module/file scanning;
  - later: DB/project registry/Kubernetes operator API.
- Adapter is replaceable without changing orchestration nodes.

## Config stream architecture (`node -> service -> adapter`)

### Node

- Starts config stream and forwards records/events into runner.

### Service

- Validates stream stage transitions.
- Aggregates typed config records in startup store.
- Emits `config.completed` when stream is fully consumed.

### Adapter

- Source backend for configuration records:
  - now: YAML file;
  - later: DB/service config API.

## Typed config records (initial target)

1. `SystemRuntimeConfigRecord`
2. `ObservabilityConfigRecord`
3. `ExecutionGroupConfigRecord`
4. `NodeConfigRecord`
5. `ConfigStreamCompletedEvent`

Each record keeps:

- `source` (file/db/api);
- `section`;
- stable `record_id`;
- payload dataclass with validated shape.

## Startup barrier contract

- Barrier remains closed until both discovery and config streams are completed.
- Closed barrier means:
  - no lifecycle spawn;
  - no business source ingress activation.
- Barrier state is observable (logs + metrics + traces).

## Observability requirements for startup

Startup stages must emit:

- lifecycle logs to root stdout rail;
- tracing events for node/service transitions;
- metrics for stage latency (discovery, config stream, barrier wait, spawn).

This is required to diagnose slow or blocked startup deterministically.

## Long-running runtime mode (API use-case)

The same startup/barrier model supports daemon operation:

- runner loop stays alive (`run_forever` / control-plane stop);
- API ingress pushes envelopes into runtime queue;
- startup barrier still protects against early request handling.

No dependency on FastAPI internals is required at runner/core level.
