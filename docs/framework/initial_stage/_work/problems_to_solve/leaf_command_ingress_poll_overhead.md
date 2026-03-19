# Problem: Leaf Command Ingress — Tick-Based Polling Overhead

**Observed symptom:** real asyncio leaf processes show +18–20ms latency vs SimPy baseline.
Traced to the scheduler-driven `command_ingress` polling chain.

---

## Full Call Chain

```
AsyncRunner.run_until_stopped_async()          runner.py:1330–1351
  └─ await asyncio.sleep(poll_timeout)         runner.py:1344   ← tick #1, default 10ms
  └─ await self.run_async()                    — drains queue to empty

    PlatformScheduler (node inside runner)
      └─ interval_seconds=0.01                 system_nodes.py:199  ← tick #2
      └─ fires BootstrapControl → target=source_name

        LeafCommandIngressSourceNode.__call__()  system_nodes.py:211
          └─ poll_timeout_seconds = 0.0         system_nodes.py:226  ← NON-BLOCKING
          └─ await poll_next_message_for_lane_async(...)

            DefaultLeafCommandChannelIngressService
              └─ poll_next_message_for_lane_async()  channel_services.py:161
                   return self.poll_next_message_for_lane(...)   ← NOT actually async

                poll_next_message_for_lane()    channel_services.py:138
                  └─ _recv_from_lane(timeout=0.0)   ← non-blocking pipe check
                  └─ returns None or 1 message
```

---

## Three Specific Problems

### 1. Double tick — minimum latency is 2× interval

`run_until_stopped_async` sleeps `poll_timeout` (10ms) between iterations.
Inside each iteration the scheduler also has `interval_seconds` (10ms).
They are not synchronised. A message arriving in the pipe immediately after a tick
has to wait until the next full cycle.

This is the `+18–20ms` observed in ring_blocking_sim experiments:
4 stages × 5ms tick = 20ms overhead minimum.
SimPy had no ticks — 0ms overhead, explaining the divergence.

### 2. `poll_next_message_for_lane_async` is not actually async

[channel_services.py:161–172](../../../../../../src/stream_kernel/execution/orchestration/lifecycle/leaf/command/channel_services.py)
— the method is declared `async` but internally just calls the synchronous version:

```python
async def poll_next_message_for_lane_async(self, ...) -> object | None:
    return self.poll_next_message_for_lane(...)   # blocks event loop
```

If the underlying `recv_buffered` on the pipe blocks even for microseconds,
the event loop is frozen during that time. No concurrency is possible at that moment.

### 3. Scheduler returns at most 1 message per tick

[system_nodes.py:261](../../../../../../src/stream_kernel/execution/orchestration/control_plane/leaf/system_nodes.py)
— `return [next_message]`, one element. If 100 messages have accumulated in the pipe,
they are drained one per tick. At tick=10ms: max throughput through `command_ingress`
= 100 msg/s.

---

## Root Cause

`command_ingress` was designed for **sparse control-plane events** (stop, configure,
handshake) — 10ms latency and 100 msg/s throughput are fine there. But data-plane
traffic is routed through the same scheduler-driven polling mechanism, turning the
scheduler tick into a throughput bottleneck.

**Data-plane needs a separate event-driven path — without a fixed tick.**

---

## Implemented (2026-03-19)

Implemented a drain-style ingress refactor to reduce per-tick fan-out:

1. Leaf switched from per-lane scheduler jobs to one drain source job:
`source:system.ipc.ingress:drain`
- One scheduler task per leaf process now polls all required lanes in RR.
- Control/data/observability lanes are polled via one node call with bounded
  `max_messages_per_poll`.
- `source:system.cp.command_ingress:data` path removed.

2. Root switched from per-worker-per-lane source jobs to one drain source:
`source:system.cp.root_leaf_ingress`
- One scheduler job polls configured `(worker_id, lane)` specs in RR.

3. Backward compatibility in source node class preserved:
- Default `max_messages_per_poll=1` for existing single-lane tests/usage.
- Higher drain budget is enabled only by control-plane plan wiring.

---

## Remaining Bottlenecks After Refactor (2026-03-19)

The drain-budget refactor addresses problem #3 (1 msg/tick) but leaves four issues intact.

---

### R-1. Three-layer tick stack — still present

The fundamental layering is unchanged. A message now travels through three independent
sleep/poll boundaries before reaching the runner:

```
Layer 1 — _reader_loop background thread         ipc_adapters.py:486
  └─ _stopping.wait(poll_interval_seconds=5ms)   ← when pipes empty
  └─ drains OS pipe bytes into _PipeReceiveBuffer

Layer 2 — PlatformScheduler fires ingress node   system_nodes.py:199
  └─ interval_seconds=10ms
  └─ reads from _PipeReceiveBuffer (already populated by Layer 1)

Layer 3 — AsyncRunner outer loop                 runner.py:1344
  └─ await asyncio.sleep(poll_timeout=10ms)
  └─ triggers run_async() which lets the scheduler fire
```

Worst-case end-to-end pipe→runner latency: 5ms + 10ms + 10ms = **25ms**.
There is no notification channel from Layer 1 → asyncio event loop when new data
arrives in `_PipeReceiveBuffer`. The runner wakes on a timer, not on data.

---

### R-2. Fake-async poll not fixed — event loop still blocked per budget iteration

Both implementations still delegate `_async` to sync:

- Leaf: `channel_services.py:161–172`
  ```python
  async def poll_next_message_for_lane_async(self, ...) -> object | None:
      return self.poll_next_message_for_lane(...)   # no await, no yield
  ```
- Root: `leaf_ingress_service.py:161–172`
  ```python
  async def poll_next_leaf_ingress_for_worker_lane_async(self, ...) -> object | None:
      return self.poll_next_leaf_ingress_for_worker_lane(...)   # same
  ```

The drain budget loop in the ingress node calls this N times:

```python
for _ in range(budget):          # up to 64 (root) or 1 (leaf default)
    polled = await self._poll_one_message(...)   # fake await — no yield
```

With `budget=64` and 5 specs (worker × lane combinations), each empty iteration
runs 5 synchronous `recv_buffered(timeout=0.0)` calls back-to-back. That is up to
320 synchronous pipe checks in a single scheduler invocation with zero yields to
the event loop. Other coroutines (business processing tasks) cannot run during this
window.

---

### R-3. `max_messages_per_poll=1` default for leaf command ingress — unchanged

`ControlPlaneLeafCommandIngressSourceNode.max_messages_per_poll` defaults to `1`
(`system_nodes.py:215`). The drain budget is opt-in via plan-builder wiring.
Any code path that constructs this node without explicitly setting the budget
still gets the original 1-msg/tick behaviour.

The new `LeafRuntimeIngressSourceNode` (data/observability lanes) has no default
budget value visible in the class — its budget also comes from plan wiring. If the
plan builder does not configure `max_messages_per_poll`, both node types fall back
to budget=1 via `max(1, int(self.max_messages_per_poll))`.

---

### R-4. `_reader_loop` — coarse lock + blocking `poll()` per target

Every `_reader_loop` iteration acquires a global `self._lock` to snapshot targets
and endpoints (`ipc_adapters.py:488–491`):

```python
with self._lock:
    targets = list(self._reader_targets)
    endpoints = {tid: ep for tid, ep in self._endpoints.items() if tid in self._reader_targets}
```

With 5 lanes × N leaf workers = potentially 10–20 targets, this dict comprehension
runs under lock every 5ms. The lock is shared with `attach_endpoint` / `allocate_endpoints`
which can be called from the main process during startup — creating contention.

Inside `_drain_pipe`, `_PipeEndpoint.recv()` acquires `_recv_lock` and calls
`connection.poll(remaining)` where `remaining` comes from `_drain_pipe`'s timeout
parameter. If that timeout is > 0, the `_reader_loop` thread blocks on the OS
`multiprocessing.Connection.poll()` syscall for up to `remaining` seconds,
preventing it from draining other targets during that window.

---

### Summary Table

| problem | status after refactor | blocking impact |
|---------|----------------------|-----------------|
| 1-msg/tick limit (leaf command ingress) | **opt-in fix only** — default still 1 | throughput cap |
| 1-msg/tick limit (root leaf ingress) | fixed — default 64 | — |
| Fake-async poll in leaf channel service | **not fixed** | event loop freeze per drain iteration |
| Fake-async poll in root ingress service | **not fixed** | event loop freeze per drain iteration |
| Three-layer tick stack (5ms + 10ms + 10ms) | **not fixed** | 25ms worst-case latency floor |
| No wake-on-data from `_PipeReceiveBuffer` | **not fixed** | always waits full tick even when data ready |
| Budget drain loop holds event loop | **introduced by refactor** | N×M sync calls without yield |
| `_reader_loop` coarse lock per iteration | pre-existing | contention at startup / high lane count |

---

## Update After Fix Pass (2026-03-19, later)

### Fixed / mitigated

1. **R-2 (fake async wrappers)**
- `poll_next_message_for_lane_async` (leaf) and
  `poll_next_leaf_ingress_for_worker_lane_async` (root) now do an explicit
  `await asyncio.sleep(0)` before sync poll call.
- This removes the "no-yield" behavior inside drain-budget loops and prevents
  long contiguous event-loop monopolization by ingress pollers.

2. **R-3 (leaf default budget=1)**
- `ControlPlaneLeafCommandIngressSourceNode.max_messages_per_poll` default
  changed from `1` to `64`.
- Plan wiring still can override, but "forgot to set budget" no longer falls
  back to 1-msg/tick behavior.

3. **R-4 (`_reader_loop` lock/poll behavior)**
- Reader loop now:
  - snapshots endpoints outside the hot lock path,
  - uses `multiprocessing.connection.wait(...)` when available to wake only on
    ready connections,
  - uses an explicit wakeup event for topology changes (`attach/register`),
  - drains via non-blocking readiness path (`poll(0)` + `recv_ready`) instead of
    timeout-based recv.
- This removes per-target timeout polling and lowers lock contention.

### R-1 status (fixed in this pass)

1. **R-1 (three-layer tick stack)**
- `AsyncRunner.run_until_stopped_async` switched from fixed `await asyncio.sleep(poll_timeout)`
  to queue-driven `wait_for_item_async(...)` when queue supports it.
- `QueuePort` now has async wait API with default fallback, and both concrete
  runtime queues (`InMemoryQueue`, `TcpLocalQueue`) implement signal-based async
  waiter wakeup (`push` / `close` -> wake waiters).
- Result: the runner idle tick layer is removed for standard runtime queues;
  effective fixed stack is reduced to transport/scheduler layers.


## State Verification (2026-03-19, post-fix-pass)

Checked actual code after the "Update After Fix Pass" was applied.

### What is genuinely fixed

**R-1 (runner tick layer) — confirmed fixed.**
`runner.py:1332–1356`: `wait_for_item_async` is resolved from the queue before the
loop starts. Inside the loop, `await wait_for_item_async(poll_timeout)` is called
instead of `await asyncio.sleep(poll_timeout)`. `InMemoryQueue.wait_for_item_async`
(`work_queue.py:115–137`) creates a `Future`, appends it to `_async_waiters`, and
awaits it via `asyncio.wait_for(waiter, timeout=poll_timeout)`. `push()` calls
`_notify_async_waiters_unlocked` → `loop.call_soon_threadsafe(...)` which resolves
the future from any thread. Runner layer tick is gone for `InMemoryQueue`.

**R-2 (fake async) — confirmed fixed.**
`channel_services.py:169–172`: `await asyncio.sleep(0)` before the sync call.
`leaf_ingress_service.py:169–172`: same. Each drain-budget iteration now yields once.

**R-3 (leaf default budget) — confirmed fixed.**
`system_nodes.py:215`: `max_messages_per_poll: int = 64`.

**R-4 (_reader_loop) — confirmed fixed.**
`ipc_adapters.py:511–581`: loop now calls `_snapshot_reader_endpoints()` (lock taken
once, outside hot path), then `_connection_wait(connections, timeout=poll_interval_seconds)`
which is `multiprocessing.connection.wait` — `select`/`kqueue`/`epoll` under the hood.
Reader wakes immediately when OS pipe has data, not on a fixed timer. `_reader_wakeup`
Event used for topology changes (new endpoint attached).

---

### What still limits latency

**Remaining bottleneck: the scheduler layer (0–10ms) is the new floor.**

The two-layer stack after all fixes:

```
Layer 1 — _reader_loop thread
  _connection_wait(connections, timeout=5ms)
  → wakes immediately on OS pipe data (select/epoll)
  → drains bytes into _PipeReceiveBuffer
  (no notification to asyncio from here)

Layer 2 — PlatformScheduler fires ingress node every 10ms
  → reads _PipeReceiveBuffer
  → puts message into work_queue
  → work_queue.push() → _notify_async_waiters → runner wakes
```

`_connection_wait` is reactive (wakes on data, not on timer), so Layer 1 latency
is ~0 when data is present. Layer 2 is still timer-driven: worst-case 10ms between
data landing in `_PipeReceiveBuffer` and the scheduler firing the ingress node.

**The gap:** `_reader_loop` has no path to notify asyncio when it places a message
into `_PipeReceiveBuffer`. The runner's `wait_for_item_async` is signal-based, but
signals only fire when `work_queue.push()` is called — which only happens after the
scheduler fires the ingress node. The signal mechanism is correct; it just fires one
hop too late.

To eliminate Layer 2: the IPC receive path would need to push directly into the work
queue (bypassing the scheduler-driven ingress node), then `_reader_loop` could call
`work_queue.push()` and the runner would wake with ~0ms extra latency. This requires
restructuring the ingress architecture — the scheduler-driven source node pattern is
not compatible with a fully event-driven IPC ingress.

**`await asyncio.sleep(0)` yields once per message, not per spec sweep.**
`_poll_one_message` iterates over all (lane, worker) specs in a Python for-loop,
calling `await poll_for_lane_async(...)` for each. Each such call yields once via
`asyncio.sleep(0)`. Under a budget of 64 with 4 lanes × 1 worker, a full empty sweep
triggers 4 yields. This is acceptable — but means the ingress node monopolises the
event loop for 4 scheduling callbacks before concluding "nothing to receive".

---

## Eliminating the Scheduler Layer — Is It Realizable?

The remaining 0–10ms comes from this asymmetry:

- `_reader_loop` (thread) drains OS pipe → `_PipeReceiveBuffer` immediately on data
- asyncio runner wakes on `work_queue.push()` — but nobody calls `push()` until the scheduler fires

The instinct that "this is unrealizable" comes from a real constraint: asyncio is
single-threaded, and you cannot safely call most asyncio primitives from a background
thread. But `loop.call_soon_threadsafe()` exists precisely to bridge this. It is already
used — `_notify_async_waiters_unlocked` in `work_queue.py:156` calls it to wake the runner
from `push()`. The mechanism is proven. It just needs to be wired one hop earlier.

---

### Why the gap exists

`_reader_loop` fills `_PipeReceiveBuffer` but does not know:
1. which asyncio event loop is running (the runner's loop)
2. how to trigger the ingress node without going through the scheduler

Both are solvable.

---

### Option A — Wakeup callback injected into the adapter (minimal change)

The adapter (`PipeExecutionIpcTransportAdapter`) gets an optional callback:

```python
def register_data_available_callback(
    self,
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[], None],
) -> None:
    self._data_available_loop = loop
    self._data_available_callback = callback
```

In `_reader_loop`, after `_drain_pipe` returns `drained > 0`:

```python
if drained > 0 and self._data_available_callback is not None:
    self._data_available_loop.call_soon_threadsafe(self._data_available_callback)
```

The callback (registered by the ingress node or runner at startup) enqueues a
`BootstrapControl` into the work queue, triggering the ingress node immediately.
Scheduler remains as a fallback heartbeat (e.g. every 100ms) to handle edge cases
like missed wakeups on process startup.

**What changes:** adapter + ingress node wiring at startup. Ingress node and routing
logic untouched. Fully cross-platform (`call_soon_threadsafe` works everywhere).

**Residual latency:** `_connection_wait` → drain → `call_soon_threadsafe` →
event loop schedules callback → ingress node runs → `work_queue.push()` → runner wakes.
This is effectively a single asyncio scheduling round-trip: ~0.1–0.5ms on a quiet loop.

---

### Option B — asyncio fd watcher (Unix only)

Replace `_reader_loop` thread entirely. Register a read callback on the pipe fd:

```python
loop.add_reader(pipe_connection.fileno(), _on_pipe_readable)
```

`_on_pipe_readable` fires synchronously inside the event loop when the OS signals
the fd is readable. It drains the pipe non-blocking (calling `recv_bytes` in a loop
until `poll(0)` returns False), pushes messages directly into the work queue or
calls the ingress node inline.

**Advantage:** eliminates the background thread and `_PipeReceiveBuffer` entirely.
No cross-thread coordination needed — everything is in the event loop thread.
Latency from OS pipe readable → message in work queue: one event loop iteration (~0ms).

**Disadvantage:** `add_reader` not available on Windows (SelectorEventLoop on
Windows uses WinAPI handles, not fds). Would need a platform abstraction:
`_reader_loop` on Windows, `add_reader` on Unix.

`multiprocessing.Connection.fileno()` works on Unix. Verified: Python docs confirm
`Connection.fileno()` returns the underlying OS fd.

---

### Option C — asyncio.StreamReader over the pipe fd (Unix, structured)

```python
loop = asyncio.get_running_loop()
read_transport, protocol = await loop.connect_read_pipe(
    lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()),
    os.fdopen(connection.fileno(), 'rb', buffering=0),
)
```

The `StreamReader` integrates with the event loop's native I/O polling. Data arrives
as bytes; the existing codec layer decodes it into `ExecutionIpcMessage`. This is the
most "asyncio-native" approach but requires wrapping the pipe fd as a file object and
handling the codec framing (length-prefixed or pickle boundary detection) at the
stream level.

More work, but produces a clean `async for message in reader:` API.

---

### Recommended path: Option A first, Option B later

Option A is the smallest change to the existing architecture:

1. Add `register_data_available_callback(loop, fn)` to the adapter
2. In `DefaultLeafProcessEntryOrchestrationService.run()`, after building the runner,
   call `register_data_available_callback` with the runner's loop and a closure that
   calls `work_queue.push(BootstrapControl(target=ingress_source_name))`
3. Reduce scheduler interval for ingress to 100ms (heartbeat only, not primary trigger)
4. Expected result: 0–10ms Layer 2 drops to ~0.5ms

Option B is the right long-term direction for Unix deployments, but requires platform
branching and more invasive changes to the transport layer.

The key realization: **`call_soon_threadsafe` is already used correctly in
`work_queue.py`. The identical pattern applied one hop earlier (in the adapter, on
drain) eliminates the last timer-driven layer without any new concurrency primitives.**

### Decision and rollout (2026-03-19)

- Implemented now: **Option A** (cross-platform) — source ingress nodes register
  per-target `data_available` callbacks in IPC transport; background reader invokes
  them after drain; callback pushes one coalesced `BootstrapControl` directly into
  runner queue.
- Kept: scheduler job for ingress as heartbeat/fallback.
- Not implemented yet: Option B/C (`add_reader` / `connect_read_pipe`) due
  Unix-only constraints.
- Platform strategy for future:
  - Windows (`os.name == "nt"`): keep Option A.
  - Linux/macOS: optionally add Option B under feature flag and keep A as fallback.

---

## Relevant Files

| file | role |
|------|------|
| `src/stream_kernel/execution/runtime/runner.py:1317` | `run_until_stopped_async` — now uses `wait_for_item_async` |
| `src/stream_kernel/integration/work_queue.py:115` | `InMemoryQueue.wait_for_item_async` — Future-based, woken by `push()` |
| `src/stream_kernel/execution/orchestration/control_plane/leaf/system_nodes.py:215` | `max_messages_per_poll=64` default |
| `src/stream_kernel/execution/orchestration/lifecycle/leaf/command/channel_services.py:169` | `await asyncio.sleep(0)` before sync poll |
| `src/stream_kernel/execution/orchestration/control_plane/root/leaf_ingress_service.py:169` | same, root side |
| `src/stream_kernel/execution/transport/carriers/ipc/ipc_adapters.py:511` | `_reader_loop` — `_connection_wait` + `_reader_wakeup` |

---

## OS I/O Readiness: What Options B and C Actually Get From the Kernel

Both options rely on the same OS primitive: **I/O readiness notification**.

When another process writes to a pipe, the kernel marks the file descriptor (fd) as
"readable". You can then ask the kernel: "give me the list of fds that have data right
now". This is done via:

- **Linux:** `epoll_wait()` — waits on a set of fds, returns only those with data
- **macOS/BSD:** `kqueue` + `kevent()` — same idea, different API
- **Windows:** WinAPI `WaitForMultipleObjects` — but works on HANDLEs, not fds; pipes
  behave differently

Python's asyncio uses exactly this underneath — `SelectorEventLoop` on Linux wraps
`epoll`, on macOS wraps `kqueue`.

### Option B — `loop.add_reader(fd, callback)`

```python
loop.add_reader(connection.fileno(), _on_pipe_readable)
```

You tell the event loop: "register this fd in epoll/kqueue. When the kernel says data
is available — call `_on_pipe_readable` synchronously inside the next event loop
iteration."

Inside asyncio this looks like:

```
epoll_wait([fd1, fd2, ...], timeout=...) → [fd2 readable]
  → call callback for fd2 → your _on_pipe_readable()
  → inside: recv_bytes() without blocking — data is guaranteed to be there
```

No background thread. No polling. The OS wakes the event loop at the right moment.
Latency from "data in pipe" to "callback called": one `epoll_wait` iteration —
typically tens of microseconds.

### Option C — `loop.connect_read_pipe()`

A wrapper over the same `add_reader`, but with buffering and protocol abstraction:

```python
transport, protocol = await loop.connect_read_pipe(
    lambda: asyncio.StreamReaderProtocol(reader),
    pipe_file,
)
```

asyncio registers the fd in epoll itself, reads bytes when they appear, and puts them
into a `StreamReader` buffer. You get a clean async API: `chunk = await reader.read(n)`.

Difference from B: in B you write `_on_pipe_readable` yourself and call `recv_bytes()`
yourself. In C asyncio does it for you and gives a standard stream interface. More
convenient, but harder to wire with the existing pickle/length-prefix framing on the
pipe.

### Why Windows is a separate story

Windows pipes have no fd in the Unix sense — they have HANDLEs. There is no `epoll`.
asyncio on Windows uses `ProactorEventLoop` (over IOCP — I/O completion ports), which
works differently: not "fd is ready to read" but "read operation has completed".
`add_reader` is not available there.

This is exactly why Option A (`call_soon_threadsafe` from `_reader_loop`) is the
cross-platform answer: it doesn't touch fd at all, just uses the thread→asyncio bridge
that works everywhere. And the latency gain is nearly equivalent to Option B — the
only remaining overhead is the `_connection_wait` wakeup (~0ms on data) plus one
`call_soon_threadsafe` scheduling round-trip (~0.1–0.5ms).

---

## Why Polling Was the Default (and Why It Always Is)

**`multiprocessing.Pipe()` is not presented as "fd for epoll".**
The docs show `conn.recv()` — a blocking call. The canonical pattern is
`while True: msg = conn.recv()` in a background thread. That `connection.fileno()`
returns a real Unix fd you can hand to `add_reader` is not documented prominently
and not obvious.

**multiprocessing is thread-oriented by history.**
The module was designed alongside `threading`. "Background thread reads the pipe" is
the natural pattern. asyncio inside multiprocessing is a relatively new combination
with no established cookbook.

**Polling is easier to reason about and debug.**
Polling is deterministic: "check every N ms". epoll-based code is reactive and requires
correctly handling `EPOLLIN`, `EPOLLHUP`, `EPOLLRDHUP` — especially when a child
process crashes or closes the pipe unexpectedly. Getting all edge cases right without
regressions is harder.

**10ms overhead is invisible until you measure.**
Before benchmarks the latency is "fine". Only after SimPy simulations with real
measurements did the +18–20ms show up as a systematic overhead rather than noise.
The gap between simulation and reality is what surfaced it.

**Cross-platform concern eliminates `add_reader` early.**
If you're thinking about Windows compatibility from the start, `add_reader` drops off
the table immediately. Polling works everywhere. This pushes you toward Option A
(`call_soon_threadsafe`) which is also nearly as fast as Option B and fully
cross-platform — so it's likely the correct final answer even if Linux-only deployment
is acceptable.

The standard evolution: "works" → measure → "why so slow" → dig to epoll.

---

## Option A Implementation Review

### What was implemented

`PipeExecutionIpcTransportAdapter.register_data_available_callback(target_id, callback)`
stores callbacks per target_id. After `_drain_pipe` returns `drained > 0`, `_reader_loop`
calls `_notify_data_available(target_id)` which invokes each registered callback.

Ingress nodes (`ControlPlaneLeafCommandIngressSourceNode`,
`ControlPlaneRootLeafIngressSourceNode`) register `_on_data_available` in `initialize()`.
`_on_data_available` pushes a coalesced `BootstrapControl` into the work queue:

```
_reader_loop thread
  → _drain_pipe: drained > 0
  → _notify_data_available(target_id)     ← lock released before calling
    → _on_data_available()
      → _wakeup_lock: check _wakeup_pending
      → queue.push(BootstrapControl)
        → InMemoryQueue._notify_async_waiters_unlocked()
          → loop.call_soon_threadsafe(...)  ← thread→asyncio bridge
            → runner wakes
```

### What is correct

**Coalescing via `_wakeup_pending`** — if 50 messages arrive in one `_drain_pipe` call,
`_notify_data_available` fires 50 times but only the first `_on_data_available` reaches
`queue.push()`. The other 49 see `_wakeup_pending=True` and return immediately. Exactly
one `BootstrapControl` enters the queue per burst.

**`_clear_wakeup_pending()` at line 253 — at the start of `__call__`, before the drain
loop.** This is the correct placement. The flag is cleared before draining, so any new
data arriving from `_reader_loop` during the drain cycle will register a fresh wakeup
immediately. No messages can be missed.

**Lock ordering is safe.** `_notify_data_available` releases `self._lock` before calling
callbacks. `_on_data_available` releases `_wakeup_lock` before calling `queue.push()`.
No nested locks. `InMemoryQueue.push()` handles the thread→asyncio bridge internally via
`call_soon_threadsafe`. The chain is fully thread-safe as written.

**Callback registered in `initialize()`, not in `__call__`.** One-time registration at
startup, zero overhead on every invocation.

### The subtlety: callback executes in the IO thread

`_notify_data_available` calls the callbacks directly from `_reader_loop`:

```python
for callback in callbacks:
    callback()    # executes in IO thread, not in the asyncio event loop thread
```

This is safe today because `_on_data_available` only uses `threading.Lock` and
`queue.push()` — both thread-safe. The asyncio bridge (`call_soon_threadsafe`) is
invoked internally by `InMemoryQueue.push()`, not directly by `_on_data_available`.

The risk: if anyone adds a direct asyncio primitive call inside `_on_data_available`
in the future — `asyncio.get_event_loop()`, an `asyncio.Event`, any object designed
for single-threaded asyncio use — it will silently corrupt state or produce a race,
because the code runs in the wrong thread.

### How to make it safer without losing Option A semantics

Pass the event loop at registration time and dispatch via `call_soon_threadsafe`:

```python
# In adapter — store (loop, callback) pairs:
def register_data_available_callback(
    self,
    target_id: str,
    callback: Callable[[], None],
    loop: asyncio.AbstractEventLoop | None = None,
) -> bool:
    ...
    callbacks.append((loop, callback))

def _notify_data_available(self, target_id: str) -> None:
    for loop, callback in callbacks:
        if loop is not None:
            loop.call_soon_threadsafe(callback)  # runs in asyncio thread
        else:
            callback()                           # fallback: direct call
```

Loop reference obtained in `initialize()` which runs in asyncio context:

```python
def initialize(self) -> list[object]:
    self._register_data_wakeup(loop=asyncio.get_event_loop())
```

This is the same pattern already used in `InMemoryQueue.wait_for_item_async`
(`work_queue.py:117`: `loop = asyncio.get_running_loop()`). The pattern is proven
in the codebase; applying it one layer up makes `_on_data_available` safe to extend
with any asyncio code in the future.

### Does this change the essence of Option A?

No. Option A's essence is `call_soon_threadsafe` from the IO thread into asyncio.
That call is present in both versions — the only difference is where it sits:

```
current:  IO-thread → callback() → queue.push() → call_soon_threadsafe
safer:    IO-thread → call_soon_threadsafe(callback) → callback() → queue.push()
```

Same primitive, same point in the chain. Latency impact: zero.

### Applied follow-up (2026-03-19)

Implemented exactly this safety adjustment:

- adapter stores `(loop, callback)` for data-available subscriptions;
- `_notify_data_available` uses `loop.call_soon_threadsafe(callback)` when loop is provided;
- direct callback invocation remains fallback when loop is absent.

Root/leaf ingress nodes now pass the current running loop during registration.
So callback logic executes in asyncio thread by default, not in IO reader thread.
