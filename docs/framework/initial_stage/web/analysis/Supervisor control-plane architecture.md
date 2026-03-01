# Supervisor control-plane architecture (concept)

## Purpose

Describe a control-plane oriented supervisor architecture expressed as platform
system nodes loaded during discovery, with orchestration logic on platform rails
and without a monolithic `bootstrap.py` implementation.

This is an architectural description only; migration steps live in a separate plan.

Related:
- [Control-plane bootstrap root/leaf sequences](Control-plane%20bootstrap%20root-leaf%20sequences.md)

## Core idea

The supervisor is a **set of platform system nodes** that run inside a dedicated
control-plane runner in the owner process. These nodes are discovered with the
rest of the graph and receive injected services/stores that encapsulate process
lifecycle and transport. Business execution remains inside worker process runners.

## Roles

- Control-plane owner process
  - hosts control-plane runner executing supervisor system nodes
  - owns IPC port(s)
  - maintains control-plane state
- Worker processes
  - run business nodes and system nodes that belong to the execution graph
  - receive commands over IPC port (addressed messages)
  - reply with accepted/terminal outputs

## Service boundaries

- Process lifecycle service
  - spawn/stop/join/terminate workers
  - no transport logic inside
- IPC transport service
  - IPC port (addressed mailbox)
  - adapter swap: pipe or tcp_local
- Control-plane state store
  - in-memory baseline
  - Redis later for clustered/HA
- Supervisor system nodes
  - drive ready/start/stop transitions
  - track inflight/idle policy
  - issue control/data commands via addressed IPC port

## Supervisor control-plane nodes (proposed responsibilities)

These nodes run inside the control-plane runner and only handle **service/control**
messages (not business payloads). All mutations are append-only events in the
state store; derived state is computed from the event chain.

### 1) Init/Plan node

Input: `control.init` (one-time bootstrap signal)  
Responsibilities:
- read full runtime config (process groups + node placement);
- build initial launch plan (groups, expected workers, node lists);
- write **launch plan event** into control-plane store;
- emit `control.spawn.requested` events.

Notes:
- `control.init` can be emitted by a **discovery adapter** that aggregates discovered
  nodes/services/adapters into a KV-like snapshot and publishes a single ready signal.
- The init payload should include at least the runtime process group config, so the
  Init/Plan node can build per-group launch cards without re-running discovery.

### 2) Spawn dispatch node

Input: `control.spawn.requested`  
Responsibilities:
- call Process Lifecycle Service to spawn workers;
- emit `control.spawn.issued` events (per worker/group);
- mark group status `initializing` (via event append).

### 3) Worker bootstrap ready ingest node

Input: `control.worker.bootstrapped` (from worker)  
Responsibilities:
- append `worker.bootstrapped` event;
- fetch launch plan card for that group;
- emit `control.group.config.card` event (card contains nodes for that group);
- update group status to `starting`.

### 4) Worker ready-for-work ingest node

Input: `control.worker.ready` (worker acknowledges config card applied)  
Responsibilities:
- append `worker.ready` event;
- update group status to `ready`;
- emit `control.group.status.updated` event.

### 5) Status projector node

Input: any `control.*` event  
Responsibilities:
- build derived runtime status snapshot from append-only events;
- emit `control.runtime.status` snapshots for observers.

### 6) Orchestration gate node

Input: `control.runtime.status`  
Responsibilities:
- decide when **global start** is allowed;
- emit `control.start` when all groups are `ready`;
- enforce stop/idle policies (explicit stop > idle timeout > run completion).

## Worker-side system logic (proposed responsibilities)

Workers host execution runners and a small set of **control-plane service nodes**
that react to supervisor commands and manage local execution state.

### Platform service: Node Loading Service

Purpose: load/activate a subset of nodes with DI injection and start them under
the correct runner profile.

Responsibilities:
- accept a list of node names + target runner profile;
- resolve node callables via DI;
- build runner plan (sync/async);
- start runner with the requested node set;
- expose status/ack hooks for control-plane nodes.

### Worker control nodes

#### 1) Worker bootstrap/ready node

Input: `control.group.config.card`  
Responsibilities:
- request Node Loading Service to load the node set;
- apply runner profile and start execution;
- emit `control.worker.ready`.

#### 2) Boundary ingest node

Input: `control.data.dispatch`  
Responsibilities:
- enqueue boundary payloads into the runner work queue;
- emit `control.data.ack` with acceptance status.

#### 3) Boundary output node

Input: terminal/external outputs from runner  
Responsibilities:
- forward outputs to supervisor via data channel;
- include correlation ids and trace metadata.

#### 4) Heartbeat node (optional)

Responsibilities:
- emit `control.worker.heartbeat` at fixed interval;
- include queue depth/inflight diagnostics.

#### 5) Shutdown node

Input: `control.stop`  
Responsibilities:
- request runner drain/stop;
- emit `control.stop.ack` with `output_closed`.

## Control/data channels

- Control channel
  - bootstrap bundle, ready, start, stop, ack
  - must stay responsive under data saturation
- Data channel
  - boundary inputs and outputs
  - ack semantics for accepted payloads
  - fire-and-forget for observability traffic

## Lifecycle stages

1. Discovery and planning
2. Control-plane runner startup (supervisor nodes loaded)
3. Worker spawn and bootstrap
4. Ready handshake
5. Boundary dispatch (control-plane messages only)
6. Stop policy evaluation
7. Graceful shutdown or force terminate

## Stop policy

The control-plane owner should not stop on idle queue alone. Stop is determined
by explicit policy driven by supervisor nodes:

- explicit stop signal
- idle timeout policy
- run completion signal

The policy is a configuration contract, not hardcoded.

## Integration points

- Orchestration entrypoint stays in `lifecycle_orchestration`.
- Supervisor logic is a system graph executed by a control-plane runner.
- Transport remains an injected service; supervisor nodes are transport-agnostic.

## Non-goals

- FastAPI adapters
- Redis-backed queues
- Multi-host networking
