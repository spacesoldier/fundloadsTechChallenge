# ASGI servers and deployment topologies

This note captures practical differences between `uvicorn`, `gunicorn`, and
`hypercorn`, and maps them to framework deployment choices.

Related:

- [FastAPI interface architecture](../FastAPI%20interface%20architecture.md)
- [network interfaces expansion plan](../../_work/network_interfaces_expansion_plan.md)

---

## 1) Core distinction: app vs server vs process manager

- `FastAPI` is an ASGI application.
- An ASGI server executes it (`uvicorn`, `hypercorn`).
- A process manager (`gunicorn`) manages worker processes; it is not the app.

---

## 2) Uvicorn, Gunicorn, Hypercorn

### Uvicorn

- ASGI server focused on performance and simple ops.
- Strong default choice for HTTP/1.1 + WebSocket workloads.
- Common in dev and many production setups.

### Gunicorn

- Process manager (master + workers).
- Traditionally WSGI-first, but can run ASGI apps via ASGI workers.
- Typical combo in Python ops stacks:
  - `gunicorn` manages workers
  - each worker runs ASGI server logic (for example uvicorn worker class)

### Hypercorn

- ASGI server with strong support for modern protocol scenarios.
- Better fit when HTTP/2 behavior is a first-class requirement.
- Good candidate for planned streaming/network track.

---

## 3) Does Uvicorn run "on top of" Gunicorn?

Two valid topologies:

1. `uvicorn` standalone:
   - ASGI server directly runs app.
   - process scaling is external (container orchestrator/systemd/etc.).

2. `gunicorn + uvicorn workers`:
   - `gunicorn` supervises and scales local worker processes.
   - worker class runs ASGI serving logic.

So: `uvicorn` does not require `gunicorn`, but they are often combined.

---

## 4) Where to place framework runtime

The framework should not be tightly coupled to any one server binary.

Recommended split:

- **Control plane (web ingress/egress adapters)**:
  - receives HTTP/WS/GraphQL requests,
  - converts to framework transport models,
  - routes into execution runtime.

- **Execution plane (runner/queues/router/services)**:
  - performs DAG/routing work,
  - emits reply models/events.

This keeps web server choice replaceable (`uvicorn` vs `hypercorn`) and avoids
hardcoding transport details in core execution modules.

---

## 5) Celery and worker clusters

Do not force heavy execution onto the same event loop/thread that serves HTTP.

Typical patterns:

- web ingress adapter accepts request and enqueues execution task;
- execution workers (sync/async/celery/gpu pools) process it;
- reply is correlated by `trace_id` / `reply_to` and returned via response channel.

For long-running jobs:

- endpoint returns accepted/job-id quickly;
- client polls status or subscribes via stream/websocket;
- final payload is routed by correlation contract.

---

## 6) Practical guidance for this framework

Near term:

- keep server runtime abstraction in config (`runtime.web.server`);
- start with `uvicorn` baseline for dev/simple prod;
- add `hypercorn` profile when HTTP/2 stream tests are introduced.

Medium term:

- formalize execution backends (`execution.cpu`, `execution.asyncio`, `execution.celery`, `execution.gpu`);
- keep web adapters thin and discovery-driven;
- keep reply correlation in framework services, not endpoint-local state.

---

## 7) Open implementation questions

- How to expose server-specific knobs in config without leaking backend internals?
- Which reply strategy is default:
  - direct request/response await,
  - deferred job-id + callback/stream,
  - hybrid by endpoint type?
- What timeout/cancellation policy should be global vs endpoint-local?

