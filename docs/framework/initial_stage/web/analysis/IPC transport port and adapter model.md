# IPC transport port and adapter model

## Purpose

Define the port/adaptor model for IPC transport so that IPC traffic is
expressed on platform rails and can be swapped between `pipe` and `tcp_local`
without changing orchestration logic.

## Core requirements

- point-to-point addressing (control-plane ↔ worker);
- optional broadcast (rare);
- explicit ack semantics;
- sharding for multiple workers per group;
- transport-agnostic ports.

## Port types

### 1) `ExecutionIpcPort` (mailbox / kv_stream)

Single IPC port interface for all traffic. The **address** determines the
semantic class of the message (control-plane vs data-plane vs observability).

**Operations**
- `send(target_id, payload, *, shard_key=None, no_reply=False)` → `Ack | None`
- `recv(timeout=None)` → `Message`

**Ack policy**
- default: ack on accept;
- `no_reply=True` for fire-and-forget (observability or best-effort control).

### 2) Optional `ExecutionTopicPort` (broadcast)

Only if required for control-plane fan-out.

## Addressing model

`target_id` is a stable string that encodes intent:

- `group:<group_name>`
- `worker:<group_name>#<index>`
- `system.observability` (service process)

## Sharding model

When a group has multiple workers:

- `shard_key` is optional input (e.g., `trace_id`, `reply_to`);
- a `WorkerShardPolicy` selects the worker index:
  - `round_robin` (no key)
  - `hash(shard_key) % worker_count`
  - `sticky` (custom)

Sharding is part of routing policy, not the adapter.

## Adapter responsibilities

Adapters implement the port contract for a specific transport:

- `pipe` adapter: creates paired endpoints and uses OS pipes.
- `tcp_local` adapter: maintains local TCP connections and framed bytes.

Adapters are **dumb transport**: no routing, no policy, no orchestration.
If the transport keeps separate internal lanes, this is an adapter detail; the
port interface remains unified.

## Platform rails conventions

- Any **service** is decorated with `@service` so the framework can discover
  and instantiate it during runtime bootstrap.
- Services avoid internal ad-hoc state (no private dicts/deques for core state).
  Instead they inject platform ports and persist runtime state through those
  ports.
- Any **adapter** is decorated with `@adapter` and implements exactly one of
  the platform port interfaces. Adapters remain transport-only; orchestration
  and policy are implemented in services.

## Ack semantics

- `accepted`: receiver enqueued payload;
- `rejected`: receiver refused (backpressure / validation);
- `timeout`: sender did not observe ack within SLA.

## Buffering and backpressure policy (port-level)

Buffering belongs to the **port/service**, not the adapter. Adapters remain
transport-only. The port owns queueing and backpressure policy.

Rules:

- control-plane commands are **not buffered** and are sent immediately;
- data and observability messages are buffered **per target_id**;
- buffering is FIFO and preserves order per target_id;
- no drops by default; if bounded buffers are configured, the sender blocks.

Implementation note:

- one buffered receiver per worker process (per target_id);
- a single unbuffered receiver for control-plane messages.

## Buffered receive policy via DI

Buffering is a **receive-side** policy. The sending side stays immediate and
stateless. A consumer requests buffering at injection time.

Proposed DI shape:

- consumer declares a dependency on `ExecutionIpcPort` plus a
  `ExecutionIpcReceivePolicy` (buffer config);
- transport service registers a buffered receiver for that consumer and returns
  a port handle that reads from the buffer queue.

Receive policy fields:

- `buffer.enabled` (default true for data/observability, false for control)
- `buffer.max_items` (default unbounded)

Ack timing:

- ACK is emitted when the message is **enqueued into the consumer buffer**,
  not when the consumer finishes processing.

Transport detail:

- pipes/sockets are created before spawn and remain the underlying transport;
- buffering is local to the process and does not change pipe creation.

## Adapter batching (optional, internal)

Batching is an **internal transport concern**, not a port capability. If an
adapter supports batch emission, the transport service may batch items before
sending. The port API remains `send/recv`.

## Defaults and configuration

Configuration lives under IPC transport settings. If not provided, defaults are
computed from runtime context:

- `buffer.enabled` defaults to `true` for data/observability, `false` for control;
- `buffer.max_items` defaults to **unbounded** (no overflow policy);
- `batch.max_items` defaults to `64` unless overridden;
- `batch.flush_interval_ms` defaults to `20` for `pipe` and `50` for `tcp_local`;
- `batch.max_bytes` defaults to a safe cap (e.g., 1 MiB) if payload sizes are unknown.

All defaults are overridable in config and should be surfaced in diagnostics.

### Config shape (runtime)

`runtime.platform.execution_ipc.buffer`:

- `enabled: bool` (default `true` for data/observability)
- `batch_max_items: int` (default `64`, internal adapter batching only)
- `flush_interval_ms: int` (default `20`, internal adapter batching only)
- `per_group: { <group_name>: { enabled?, batch_max_items?, flush_interval_ms? } }`

Control-plane is unbuffered regardless of these defaults.

`runtime.platform.execution_ipc.poll_mode`:

- `timer` (default): periodic polling on the adapter loop
- `reader`: `loop.add_reader` (Linux/Unix)
- `auto`: reader + timer fallback

`runtime.platform.execution_ipc.poll_interval_ms`:

- interval for timer polling (used by `timer` and as fallback for `auto`)
- default: `5`

`runtime.platform.execution_ipc.flow_control`:

- `mode: credits | token_bucket | hybrid | none` (default `credits`)
- `credits.window_size: int` (default `8192`)
- `token_bucket.rate_per_sec: int | float` (default `20000`)
- `token_bucket.burst: int | float` (default `20000`)

Flow control is applied on the **sender** side. Credits are released when the
receiver **enqueues** messages into its local buffer (ACK is emitted by the
adapter on enqueue). Token bucket provides a rate limiter; hybrid enforces both.

`runtime.platform.execution_ipc.codec`:

- `pickle` (default): pipe messages use Python pickle for arbitrary objects.
- `bytes`: pipe messages are encoded to bytes (JSON + type tags). Requires
  payloads to be JSON-safe or one of the supported platform dataclasses
  (Envelope/TraceRecord/LogMessage/MonitoringMessage/etc).

## Buffer metrics (required)

- `ipc_queue_depth`
- `ipc_queue_oldest_age_ms`
- `ipc_queue_backlog_bytes`
`ipc_batch_*` metrics are emitted only if adapter batching is enabled.
- `ipc_ack_latency_ms`

## Notes

This model assumes control-plane ownership of routing and sharding. The IPC
ports are transport-only and remain swappable.
