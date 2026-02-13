# Web-execution process isolation and Redis backbone

This note defines a target topology where web ingress/egress and execution
workers are isolated and communicate through Redis-backed platform ports.

Related:

- [FastAPI interface architecture](../FastAPI%20interface%20architecture.md)
- [Runner loop orchestration](../Runner%20loop%20orchestration.md)
- [ASGI servers and deployment topologies](ASGI%20servers%20and%20deployment%20topologies.md)
- [Execution runtime and routing integration](../../Execution%20runtime%20and%20routing%20integration.md)

---

## 1) Why isolate web and execution

Current risk:

- blocking execution (`SyncRunner`) can starve web ingress loop if colocated naively.

Isolation goals:

- keep web process responsive under load;
- scale execution independently;
- avoid coupling web server lifecycle to runner lifecycle;
- keep correlation/reply routing deterministic.

---

## 2) Target deployment profiles

### Profile A — Local/dev fallback

- in-memory queue + in-memory kv.
- single process, optional background thread for execution loop.
- used for tests/local runs.

### Profile B — Single-host isolated processes

- web process (ASGI server) and execution process are separate.
- communication via Redis-backed queue/kv/reply channels.
- process orchestration via systemd/supervisor/container runtime.

### Profile C — Cluster/distributed workers

- web ingress nodes separate from execution worker pool.
- execution workers scaled horizontally.
- same Redis-backed contracts (or compatible broker/storage adapters).

---

## 3) Redis as platform backbone

### 3.1 Queue transport

Preferred primitive:

- Redis Streams + consumer groups (ack/replay/lag visibility).

Baseline acceptable:

- list-based queue for initial deterministic profile.

### 3.2 Context storage

Store context metadata by trace key:

- key: `ctx:{trace_id}`
- value: map/hash payload with reserved framework fields (`__trace_id`, `__seq`, etc.)
- TTL policy configurable by runtime profile.

### 3.3 Reply correlation storage

Correlation primitives:

- key/channel: `reply:{request_id}`
- waiter registry in web process maps request -> completion future.
- execution egress publishes terminal response/error payload.

---

## 4) Platform contracts to implement

Keep contracts stable; only adapters change:

- Queue contract (`push/pop/ack` semantics by profile)
- KV contract (`get/set/delete` + TTL where supported)
- Reply channel contract (publish/await/timeout)

Do not leak Redis client details into business nodes or runner core.

---

## 5) Runtime config direction

Target config shape (illustrative):

- `runtime.platform.queue.backend: memory | redis`
- `runtime.platform.kv.backend: memory | redis`
- `runtime.platform.reply.backend: memory | redis`
- `runtime.platform.redis.*`: host/port/db/auth/timeouts/stream options

Defaults:

- memory backends remain default for local/dev and deterministic challenge tests.

---

## 6) Determinism and ordering impact

- `source_seq` sink mode must remain deterministic regardless of backend.
- queue/backend migration must not alter semantic ordering guarantees.
- reply routing must preserve trace/request correlation exactly once per terminal path.

---

## 7) Failure model (minimum)

- web timeout while waiting for reply -> deterministic error response + waiter cleanup.
- worker crash/restart -> message reprocessing policy must be explicit.
- stale contexts/replies -> TTL + scavenger policy.
- observability must emit queue lag, timeout, redelivery, and drop diagnostics.

---

## 8) TDD checkpoints (summary)

`REDIS-01` queue adapter conformance vs in-memory queue contract.

`REDIS-02` kv adapter conformance vs in-memory kv contract.

`REDIS-03` reply channel conformance and timeout semantics.

`REDIS-04` web process stays responsive while execution process is saturated.

`REDIS-05` deterministic output and sink ordering preserved under Redis profile.

