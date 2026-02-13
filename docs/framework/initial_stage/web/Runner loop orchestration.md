# Runner loop orchestration

This document defines how to run framework execution loops alongside FastAPI
without blocking request acceptance.

Related:

- [FastAPI interface architecture](FastAPI%20interface%20architecture.md)
- [analysis/ASGI servers and deployment topologies](analysis/ASGI%20servers%20and%20deployment%20topologies.md)
- [Execution runtime and routing integration](../Execution%20runtime%20and%20routing%20integration.md)

---

## 1) Problem

`SyncRunner` is blocking by design. If executed on the same event loop/thread as
FastAPI request handling, it can starve ingress and reduce web responsiveness.

---

## 2) Target rule

Web ingress thread/event loop must stay lightweight.

- Ingress accepts request and enqueues execution input.
- Execution loops run in dedicated workers (thread/process/cluster).
- Reply routing returns response to waiting caller via correlation metadata.

---

## 3) Supported orchestration modes

### Mode A — In-process dev/small load

- FastAPI runs in main ASGI loop.
- `SyncRunner` loop runs in dedicated background thread.
- Shared in-memory queue/registry services.

Pros:
- simple setup, fast iteration.

Cons:
- limited isolation, GIL contention on CPU-heavy paths.

### Mode B — Same host, separate processes

- Web process and execution worker process are separated.
- Communication via queue/topic backend (local broker, Redis, etc.).

Pros:
- better isolation and lifecycle control.

Cons:
- more deployment plumbing.

### Mode C — Distributed worker cluster

- Web ingress process stays thin.
- Execution delegated to worker backend (for example Celery workers).
- Correlated response delivery via result channel or callback stream.

Pros:
- scale execution independently from web.

Cons:
- operational complexity.

---

## 4) Lifespan integration (FastAPI)

At `startup`:

- initialize transport adapters/services;
- start runner loops according to configured mode;
- register shutdown hooks.

At `shutdown`:

- stop intake;
- drain queues within timeout budget;
- flush/close sinks and observability channels;
- cleanup waiter registries.

---

## 5) Correlated reply model

Runner orchestration must preserve correlation contract:

1. Ingress stores `reply_to`/`trace_id`.
2. Execution emits response/error model.
3. Response adapter routes by correlation key.
4. Waiting HTTP/WS caller gets terminal result or timeout error.

---

## 6) Backpressure and admission control

Required controls:

- bounded ingress queue (or bounded broker topics),
- queue depth metrics,
- per-endpoint max in-flight waiters,
- deterministic overload policy (`429`/`503` with clear diagnostics).

---

## 7) Test cases

`RLOOP-01` blocking `SyncRunner` in dedicated thread does not block HTTP request acceptance.

`RLOOP-02` graceful shutdown drains in-flight messages within configured timeout.

`RLOOP-03` timeout path returns deterministic error and cleans waiter registry.

`RLOOP-04` overload policy triggers when queue/waiter limits are reached.

`RLOOP-05` same request/reply correlation works in all three orchestration modes.

---

## 8) Current implementation intent

- Keep `SyncRunner` as baseline blocking engine.
- Add orchestration layer that owns runner loop lifecycle.
- Keep this layer transport-agnostic and discovery/DI-driven.
- Add async/cluster runners later without changing web ingress contracts.

