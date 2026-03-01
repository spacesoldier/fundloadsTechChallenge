# IPC transport and worker lifecycle services (concept)

## Purpose

Describe the platform services that own:

- spawning/stopping worker processes, and
- IPC transport endpoints and adapters.

This document is descriptive only. Migration steps live in a separate plan.

Related:
- [Control-plane bootstrap root/leaf sequences](Control-plane%20bootstrap%20root-leaf%20sequences.md)

## Motivation

`bootstrap.py` currently mixes process management, transport, and orchestration.
We want explicit platform services + system nodes that can be injected and
swapped without rewriting supervisor logic. Bootstrap orchestration must move
to control-plane nodes (root/leaf sequences) instead of hardcoded loops.

## Service overview

## Platform rails conventions

- Any **service** is decorated with `@service` so the framework can discover
  and instantiate it during runtime bootstrap.
- Services avoid internal ad-hoc state (no private dicts/deques for core state).
  Instead they inject platform ports and persist runtime state through those
  ports.
- Any **adapter** is decorated with `@adapter` and implements exactly one of
  the platform port interfaces (stream/kv/kv_stream/queue/topic/request/response).
  Adapters are transport-only; orchestration or policy logic belongs in
  services.

### 1) Worker Lifecycle Service

**Responsibilities**
- spawn workers (create process, pass bootstrap bundle);
- stop workers (graceful + force terminate fallback);
- join/wait for shutdown;
- expose process handles and status snapshots.

**Non-goals**
- transport send/recv
- orchestration decisions

**Interaction with transport**
- lifecycle service owns pipe creation for worker channels and registers the
  parent endpoint in the endpoint store;
- IPC transport service retrieves endpoints from that store on demand and
  exposes them through IPC ports;
- lifecycle service passes the child endpoint into the spawned process.

**Spawn argument injection**
- if `stop_event_position` is provided and `stop_event` is omitted, the service
  creates a `multiprocessing.Event` (best-effort) and injects it at the requested
  position;
- `child_endpoint_position` controls where the child pipe endpoint is inserted;
- when position is omitted, the injected argument is appended.

**Stop behavior**
- if a stop event is available, `stop_worker` signals it (`set()`) and waits
  for graceful shutdown;
- if the process is still alive after the grace period, it is terminated and
  joined with a separate timeout;
- if the runtime cannot allocate an Event (sandbox limitations), the service
  proceeds without it and relies on terminate/join fallback.

### 2) IPC Transport Service

**Responsibilities**
- build IPC ports:
  - `ExecutionIpcPort` (mailbox / kv_stream)
- own transport adapters (pipe or tcp_local);
- apply port-level buffering and batching policy;
- provide send/recv and ack policy;
- enforce payload framing and size limits.
- store endpoint registry in platform KV store (in-memory baseline).

**Non-goals**
- process lifecycle
- orchestration decisions

**Initialization**
- transport service is resolved via DI and configured from runtime config;
- lifecycle service creates pipe endpoints, stores the parent endpoint, and
  passes child endpoints to spawned workers.

## Ports and adapters

Single IPC port interface; address encodes intent:

- control-plane messages (bootstrap/ready/start/stop/ack)
- data-plane messages (boundary inputs/outputs)
- observability messages (fire-and-forget)

Buffering policy:

- control-plane messages bypass buffers;
- data/observability messages are queued per target_id;
- batching is internal to transport service/adapters (not part of port API).

Instance model:

- one buffered receiver per worker process (data/observability);
- one unbuffered receiver for control-plane.

Receive-side buffering via DI:

- consumer requests buffering when the port is injected;
- transport service creates a per-consumer queue and registers it in the
  endpoint registry (platform KV store);
- dispatcher reads from the raw pipe/socket and enqueues into the buffer;
- ACK is emitted once the message is enqueued.

Pipe lifecycle note:

- pipes are created by the lifecycle service before spawn and passed into the
  child process;
- the parent endpoint is stored in the endpoint registry so the transport
  service can attach lazily when it first sees traffic for the target;
- buffering does not alter pipe creation, only local receive behavior.

Pipe read strategy:

- `runtime.platform.execution_ipc.poll_mode` selects how the pipe is drained:
  - `timer` (default): periodic poll on the adapter-owned asyncio loop.
  - `reader`: uses `loop.add_reader(fd, ...)` for edge-triggered reads.
  - `auto`: tries `add_reader` and falls back to `timer` if unsupported.
- `poll_interval_ms` applies to `timer` mode (and `auto` fallback).
- `reader` requires a readable file descriptor (Linux/Unix selector loops).
  If `add_reader` is not supported, the adapter falls back to `timer`
  and records the chosen backend for diagnostics. In `auto` mode the timer
  poll runs alongside the reader as a safety net.

Configuration:

- `runtime.platform.execution_ipc.buffer` defines defaults;
- `per_group` overrides apply to `group:<name>` receivers;
- control-plane remains unbuffered.
- `runtime.platform.execution_ipc.codec` selects pipe payload codec:
  - `pickle` (default): uses Python pickle on the pipe and accepts arbitrary objects.
  - `bytes`: uses explicit bytes framing with JSON + type tags
    for platform dataclasses; non-serializable payloads must switch to
    `pickle` or provide byte-friendly payloads.

Adapter batching:

- batching is optional and internal to transport service/adapters;
- port API remains `send/recv`.

Flow control (planned):

- credit-based window or token-bucket on the sender to throttle enqueue rate;
- ACK confirms the receiver has enqueued a message/batch;
- implemented in the IPC transport service layer and configurable via
  `runtime.platform.execution_ipc.flow_control`.

Adapters:
- `pipe` (current baseline)
- `tcp_local` (bytes framing)

## Pipe lifecycle and endpoint passing (baseline)

Pipe channels are **two-ended**, created in the owner process **before spawn**.
They are not discoverable by PID and cannot be attached to dynamically.

Baseline flow:

1. Lifecycle service creates a pipe pair `(parent_end, child_end)`.
2. Lifecycle service stores `parent_end` in the endpoint registry (KV port).
3. Lifecycle service spawns the worker process and passes `child_end` in `args`.
4. IPC transport service lazily attaches `parent_end` when it sees traffic for
   the target id.
5. All IPC messages go through the port wrapper, not direct `send/recv`.

This keeps IPC usage confined to the control-plane subsystem and makes the
transport swappable without changing orchestration logic.

## Ownership model

The supervisor (control-plane) composes these services via DI and issues
high-level commands. It does not directly touch `Pipe` or `socket` APIs.

## Control-plane lifecycle bridge (current extraction slice)

To move spawn orchestration out of `bootstrap.py`, we add a small lifecycle bridge
inside `execution.orchestration.lifecycle`:

- a system node consumes `ControlPlaneSpawnRequestedEvent`;
- a lifecycle orchestration `@service` receives that intent;
- the service injects existing platform rails:
  - `ExecutionWorkerLifecycleService` (spawn/stop contract),
  - `ExecutionIpcEndpointRegistry` (KV endpoint registry),
  - control-plane state store service.
- the service assembles the worker spawn contract (`target`, worker args, stop/child endpoint positions)
  and calls `spawn_worker(...)` on the injected lifecycle service.

Important:

- no new platform port kind is introduced for this;
- the bridge is orchestration logic over existing platform ports.
- worker spawn context is explicit (`worker_target`, bundle, poll, codec); no fallback import of
  supervisor helpers from `bootstrap.py`.
- child-bundle projection for a concrete execution group belongs to lifecycle orchestration code,
  not to the removed hardcoded supervisor path.

## Packaging direction (root/leaf split)

As control-plane/lifecycle orchestration grows, files should be grouped by
**process role** to avoid another monolithic bootstrap module.

Target layout (incremental migration):

- `stream_kernel.execution.orchestration.control_plane.root/*`
- `stream_kernel.execution.orchestration.control_plane.leaf/*`
- `stream_kernel.execution.orchestration.lifecycle.root/*`
- `stream_kernel.execution.orchestration.lifecycle.leaf/*`

During migration, aggregate modules such as `control_plane/system_nodes.py` and
`lifecycle/system_nodes.py` may remain as temporary facades, but new logic should
prefer role-local modules.

The remaining legacy `execution.orchestration.child_bootstrap` module is a
removal target: its hardcoded child initialization must be replaced by leaf-side
`@node` + `@service` flows triggered by `ControlPlaneLeafPulse`.

## State and diagnostics

Both services should expose lightweight snapshots:

- lifecycle: worker states, pid, exit status
- transport: inflight, drops, timeouts, queue depth

These snapshots are consumed by control-plane nodes and observability pipelines.
