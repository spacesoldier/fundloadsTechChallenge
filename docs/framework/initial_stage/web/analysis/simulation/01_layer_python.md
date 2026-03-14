# Layer 1 — Python: threading, asyncio, multiprocessing

## What actually runs

The system is not pure asyncio. It is a **hybrid**: multiple OS processes, each running
its own asyncio event loop, plus one background sender thread per IPC target.

```
root process (supervisor)
├── asyncio event loop (main thread)
│   ├── runner loop coroutine
│   ├── scheduler tick coroutine  ← calls recv() SYNCHRONOUSLY (blocks loop!)
│   └── control-plane system node coroutines
└── PipeExecutionIpcTransportAdapter
    └── background sender thread (per target_id)  ← drains _PipeSendBuffer → OS pipe
        NOTE: NO background reader thread — recv() drains pipe inline in asyncio

leaf process (execution.ingress#1, execution.features#1, etc.)
├── asyncio event loop (main thread)
│   ├── runner loop coroutine
│   ├── scheduler tick coroutine
│   └── leaf command coroutines (stop, boundary_execute)
└── PipeExecutionIpcTransportAdapter
    └── background sender thread (per target_id)

observability_worker process  ← separate OS process; owns OTLP/JSONL exporters
├── asyncio event loop (main thread)
│   ├── runner loop coroutine
│   └── system nodes: TraceSinkNode, LogDispatchNode, MetricDispatchNode
└── PipeExecutionIpcTransportAdapter
    └── background sender thread
```

**Critical:** `PipeExecutionIpcTransportAdapter` has NO background reader thread.
The `recv()` path drains the OS pipe **synchronously** (calls `_drain_pipe()` inline).
When called from asyncio, this blocks the event loop for up to `poll_interval_seconds`.

## IPC pipe transport: send path vs receive path

### Send path (coordinator → leaf)

```
ipc.send(target_id, payload)           [asyncio coroutine / sync call in runner]
  └─ ExecutionIpcTransportCoordinatorService.send()
       └─ _try_send_now()
            ├─ flow_control.try_acquire()   [checks credit counter; non-blocking]
            │   → True  → adapter.send() → PipeSendBuffer.enqueue()
            │   → False → _enqueue_pending() [stored in KV; flushed on ack]
            └─ PipeSendBuffer (deque + threading.Condition)
                 └─ background sender Thread per target_id
                      └─ os.pipe / mp.Pipe send() [may block if OS buffer full]
```

**Key:** `try_acquire()` on `CreditWindowFlowControlPolicy` is **non-blocking** — it either
succeeds or enqueues to pending. The sender thread is the only place that may block on OS
pipe write if the OS-level buffer is saturated (default 64 KB on Linux).

### Receive path (leaf → root recv_buffer)

```
asyncio event loop (main thread)
  └─ scheduler tick → system.scheduler.tick node
       └─ ipc.recv(target_id, timeout=0.0)
            └─ PipeExecutionIpcTransportAdapter.recv()
                 └─ _recv_from_pipe_buffer()
                      └─ _drain_pipe(endpoint, buffer, send_ack=..., ack_enabled=...)
                           ├─ endpoint.connection.poll(timeout=0)  ← check if data ready
                           ├─ if data: connection.recv_bytes() → codec.decode()
                           │    ├─ if control_signal: ack_handler()  ← credit release
                           │    └─ else: recv_buffer.enqueue(payload)
                           └─ send_ack(count) if ack_enabled    ← ACK back to sender
```

**Key difference from what one might expect:** there is NO background reader thread.
The entire pipe drain happens **synchronously inside the asyncio runner coroutine** when
`recv()` is called. This runs in the asyncio main thread, blocking the event loop.

The `_drain_pipe` calls `endpoint.connection.poll(timeout=0)` — non-blocking check first.
If no data, `buffer.recv(timeout=poll_interval_seconds)` waits up to 5ms before returning.

ACKs (credit release signals) are sent back to the sender **during** the drain call,
inside `send_ack()` → `_send_ack_for_target()` → enqueues to `_PipeSendBuffer`
(async, via sender thread). So ACK latency = time until next `recv()` call on the
receiver's end.

### Flow control: credit release path

```
background reader thread (root side)
  └─ receives ExecutionIpcControlSignal(kind="ack", count=N, target_id=data_lane_id)
       └─ ack_handler(signal)
            └─ flow_control.release(source_target_id, count)  [Condition.notify_all()]
                 └─ _flush_pending(source_target_id)
                      └─ retry pending outbound queue → adapter.send() → enqueue
```

## Stall surface 1: credit-window pending backlog

`CreditWindowFlowControlPolicy` state:

```python
_inflight: dict[str, int]          # credits consumed per target_id
_condition: threading.Condition    # lock for acquire/release
```

`acquire()` (called only during unit-test-mode blocking path — NOT in normal runtime):
```python
while _inflight[target_id] + count > window_size:
    _condition.wait(timeout=0.05)  # ← 50ms polling!
```

In normal runtime, `try_acquire()` is used (non-blocking). Credits go to pending KV.
**The pending KV queue grows without bound until ACKs arrive.**

If ACKs stop arriving (because leaf's reader is blocked, or control lane is congested),
`_pending` grows. When `_flush_pending()` runs again after an ACK, it processes entries
in order — **FIFO, unbounded backlog**.

Deadlock condition: root → leaf data lane fills OS pipe buffer → leaf sender thread blocks
on pipe write → leaf cannot process further → leaf reader thread starves → leaf never sends
ACK control signals back → root never gets credits → root pending grows →
root sender thread eventually blocks trying to flush → **circular stall**.

## Stall surface 2: OS pipe buffer saturation

Linux default pipe buffer: 64 KB (configurable up to 1 MB with `fcntl(F_SETPIPE_SZ)`).
Pickle-encoded payload can be large (dict, nested objects). If a batch of messages exceeds
the buffer, `PipeSendBuffer`'s background sender thread blocks on `connection.send_bytes()`.

**During the block:**
- No more messages are written to that target's pipe.
- The recv_buffer on the other end drains and becomes empty.
- The reader loop on the other end reads everything quickly and then waits.
- The stall is one-sided: sender blocked, receiver idle.

The `_send_retry_backoff_seconds = 0.005` (5ms) controls how often the sender retries
after a failed send. Actual blocking happens inside `multiprocessing.connection.send_bytes`
which is a raw `os.write` — blocks until the OS pipe has space.

## Stall surface 3: asyncio — threading boundary

The runner's asyncio coroutine calls `ipc.send()` and `ipc.recv()` **synchronously** —
these are not `await`-able. They call into the thread-safe `ExecutionIpcTransportCoordinatorService`
which uses `RLock`. This means:

- An asyncio coroutine that calls `recv()` with a non-zero timeout will **block the entire
  event loop** for the duration of that timeout.
- Leaf ingress nodes call `recv()` (or `recv_buffered()`) on each scheduler tick. If the
  pipe is empty, `endpoint.poll(timeout)` blocks for `poll_interval_seconds = 0.005` (5ms)
  before returning.
- At 1ms scheduler tick interval, a 5ms poll timeout means **the scheduler fires at most
  every 5ms** even if the tick is scheduled every 1ms.

## Stall surface 4: ack_handler called from reader thread → modifies asyncio state?

`_on_flow_control_ack()` is called from the **background reader thread** (not asyncio).
It calls `flow_control.release()` (thread-safe via `Condition`) and `_flush_pending()`
(thread-safe via `RLock`). This is safe.

But if `_flush_pending()` causes `adapter.send()` which causes `send_buffer.enqueue()`
on a `_PipeSendBuffer` whose sender thread has already stopped — that is a silent drop.

## Timing summary (with config values)

| Event | Frequency |
|---|---|
| Scheduler tick | every 1.0ms (`control_poll_ms`) |
| Pipe reader poll | every 5ms (`poll_interval_seconds`) |
| Credit window | 4096 per target (before proportional split) |
| Pending KV flush | on each ACK received |
| OS pipe buffer | ~64 KB (Linux default) |
| Sender retry backoff | 5ms |
