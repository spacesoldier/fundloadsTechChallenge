# Async runtime models and event loop internals

This note explains execution model details behind `async/await` in Python and
compares major runtime ecosystems used with web stacks.

Related:

- [ASGI servers and deployment topologies](ASGI%20servers%20and%20deployment%20topologies.md)
- [FastAPI interface architecture](../FastAPI%20interface%20architecture.md)
- [Runner loop orchestration](../Runner%20loop%20orchestration.md)

---

## 1) What `async/await` is and is not

- `async def` creates a coroutine function.
- Calling it returns a coroutine object (not executed yet).
- `await` is a suspension point where control returns to runtime scheduler.

Important:

- `async/await` is language syntax.
- A runtime scheduler/loop is still required to execute coroutines.
- In practice this is usually `asyncio`, but not necessarily only `asyncio`.

---

## 2) Execution model: cooperative concurrency

Python async runtimes use cooperative multitasking:

- a task runs until it reaches an `await`;
- at `await`, task suspends ("parks");
- scheduler runs another ready task;
- parked task is resumed when awaited operation completes.

Implication:

- a CPU-blocking operation inside coroutine blocks progress of other tasks on the same loop.
- async code must avoid long blocking sections (or offload them to threads/processes/workers).

---

## 3) How parking/resume works (signals/events)

At high level, event loop maintains:

- ready queue (tasks/callbacks ready now),
- timer queue (sleep/deadline callbacks),
- I/O wait registrations (socket/file descriptor readiness),
- completion callbacks for finished futures/tasks.

Typical cycle:

1. Run a ready callback/task.
2. If task hits `await` on I/O or timer, it is suspended.
3. Loop waits for OS readiness events (selector) or nearest timer.
4. On event/timer, corresponding future/callback is marked ready.
5. Task resumes from point after `await`.

OS-level readiness usually comes via selector backends:

- Linux: `epoll`
- BSD/macOS: `kqueue`
- fallback: `poll`/`select`

So yes, there is an event/signal system, but it is readiness-driven polling
through OS multiplexing APIs, not "magic threads".

---

## 4) `asyncio` internals (practical)

- default Python async runtime and ecosystem baseline;
- task objects, futures, transports/protocols, executors;
- strong framework support (FastAPI/Starlette/Uvicorn ecosystem).

Strengths:

- broad ecosystem compatibility;
- mature tooling and documentation;
- straightforward integration with ASGI servers.

Limits:

- legacy APIs and mixed styles accumulated over time;
- cancellation/structured-concurrency ergonomics weaker than Trio model.

---

## 5) Trio model

Trio emphasizes structured concurrency and cancellation correctness.

Key concept:

- nursery scopes own child tasks lifetime;
- tasks cannot "leak" outside scope silently;
- failure/cancellation behavior is explicit and safer by construction.

Strengths:

- clearer mental model for concurrent task trees;
- robust cancellation semantics.

Tradeoff:

- ecosystem integration is narrower than `asyncio` baseline.

---

## 6) Curio model

Curio is a clean coroutine kernel approach with minimalism and explicit design.

Strengths:

- conceptual clarity;
- low-level async primitives.

Tradeoff:

- smaller ecosystem/adoption for production web stacks.

---

## 7) AnyIO role

AnyIO is an abstraction layer over async backends (`asyncio` and Trio).

Purpose:

- write backend-agnostic async code;
- select backend at runtime/context.

Relevance:

- Starlette/FastAPI stack uses AnyIO concepts internally in places;
- useful when framework code must stay backend-flexible.

---

## 8) uvloop

`uvloop` is an alternative event loop implementation for `asyncio`.

It is:

- still `asyncio` programming model,
- but with a faster loop implementation.

Use case:

- throughput/latency optimization while keeping `asyncio` APIs.

---

## 9) Relationship to WSGI/ASGI

- WSGI: synchronous request/response model.
- ASGI: async-capable model (HTTP + WebSocket + lifespan events).

For this framework web direction (streaming + ws + correlated replies), ASGI is
the required baseline.

---

## 10) Multi-loop and multi-thread/process reality

In one thread:

- one running event loop at a time.

In one process:

- multiple loops are possible across different threads.

Practical guidance:

- keep web loop dedicated to ingress/egress orchestration;
- run blocking `SyncRunner` in separate thread/process/worker pool;
- use queue/port boundaries between web loop and execution workers.

---

## 11) Why this matters for platform design

To avoid event-loop starvation:

- do not execute heavy blocking pipeline logic in FastAPI request loop;
- isolate execution plane from web ingress plane;
- keep correlation routing explicit (`trace_id`, `reply_to`, waiter registry).

This aligns with:

- [Runner loop orchestration](../Runner%20loop%20orchestration.md)
- [ASGI servers and deployment topologies](ASGI%20servers%20and%20deployment%20topologies.md)

---

## 12) Test ideas for runtime-model assumptions

`ASYNC-01` blocking section in coroutine delays unrelated requests on same loop
(characterization test).

`ASYNC-02` moving blocking runner to dedicated worker thread restores web
responsiveness.

`ASYNC-03` cancellation/timeouts propagate deterministically through correlation
waiters.

`ASYNC-04` backend abstraction path (AnyIO-compatible utility layer) keeps
core framework contracts unchanged.

