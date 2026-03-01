# IPC transport ports and adapters (TDD plan)

## Purpose

Implement IPC transport ports/adapters/services for addressed IPC traffic with
explicit ack semantics and sharding, without relying on doc-file presence tests.

## Scope

This plan covers:

- port contracts (`ExecutionIpcPort`);
- adapters (`pipe`, `tcp_local`);
- IPC transport service and DI wiring;
- sharding policy contract.

Out of scope:

- supervisor control-plane refactor;
- FastAPI or Redis integration.

## Status

- Phase A partially implemented: port contract exists, but `rejected|timeout` ack variants are not modeled yet.
- Phase B completed: pipe adapter roundtrip + codec coverage (pickle/bytes + rejection for unsupported payloads).
- Phase E completed: IPC transport service wiring, endpoint registry lazy attach, DI bindings for parent/child.
- Phase F completed: receive-side buffering, metrics, DI-driven buffer registration, ACK on enqueue, buffer config validation.
- Additional platform runtime config added:
  - `runtime.platform.execution_ipc.poll_mode` / `poll_interval_ms` (runtime-only, no adapter settings).
  - `runtime.platform.execution_ipc.flow_control` (credits/token_bucket/hybrid/none).
- Docs updated:
  - `docs/framework/initial_stage/web/analysis/IPC transport and worker lifecycle services.md`
  - `docs/framework/initial_stage/web/analysis/IPC transport port and adapter model.md`

## Phase A — Contract tests (RED)

Write tests that define the port contracts in terms of behavior:

- `ExecutionIpcPort.send()` returns `Ack` with `accepted|rejected|timeout` (unless `no_reply=True`);
- `send(no_reply=True)` returns `None`;
- `recv()` delivers `(target_id, payload, metadata)` envelope;
- timeouts are deterministic and categorized.
Status: partially implemented (only `accepted` acks today).

## Phase B — Pipe adapter tests (RED)

Tests for pipe-based adapter behavior:

- sends are delivered to the correct child endpoint;
- multiple endpoints can be created and used independently;
- `no_reply=True` does not wait for ack;
- invalid payloads are rejected deterministically.
- codec selection:
  - `bytes` codec roundtrips supported platform payloads without pickle;
  - `bytes` codec rejects non-serializable payloads with a clear error;
- `pickle` codec roundtrips arbitrary objects.
Status: done.

## Phase C — TCP local adapter tests (RED)

Tests for `tcp_local` adapter:

- framed bytes roundtrip without pickle;
- TTL / signature / nonce errors propagate as transport rejects;
- `max_payload_bytes` enforced before decode;
- ack semantics identical to pipe adapter.
Status: pending.

## Phase D — Sharding policy tests (RED)

Tests for sharding policy:

- round-robin selection for group targets without shard key;
- stable hash selection for `shard_key`;
- deterministic selection for fixed worker counts.
Status: pending.

## Phase E — IPC transport service tests (RED)

Tests for service wiring:

- service builds IPC port(s) from config;
- lifecycle service registers pipe endpoints in KV port;
- IPC transport service lazily attaches endpoints from KV when first used;
- DI resolves the service in both parent and child contexts.
Status: done.

## Phase F — Buffering and batching tests (RED)

Tests for port-level buffering and backpressure:

- control-plane messages bypass buffering and are sent immediately;
- data/observability messages are queued per target_id;
- default buffer is unbounded (no drops, no rejections);
- metrics expose queue depth and oldest age.
- transport service registers buffered receivers per worker in platform KV store.
- receive-side buffer is requested at DI injection time (not per `recv` call);
- ACK is emitted when a message is enqueued into the consumer buffer.
- runtime config validates `execution_ipc.buffer` defaults and per_group overrides.
Status: done.

## Phase G — GREEN implementation

Implement minimal code to satisfy Phases A–F without touching supervisor logic
outside the transport boundary.
Status: partial (Phases C/D remain).

## Phase H — Refactor

Extract shared helpers:

- ack envelope schema;
- payload codec helpers;
- shard policy interface.
Status: pending.

## Done criteria

- All tests pass without checking md file presence.
- Pipe and tcp_local adapters share identical port semantics.
- Sharding policy is deterministic and pluggable.
