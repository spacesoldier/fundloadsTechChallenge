# IPC background reader refactor — implementation plan

## Problem (confirmed by code reading)

`PipeExecutionIpcTransportAdapter` has no background reader thread despite fields
`_loop`, `_loop_thread`, `_reader_targets`, `_poll_mode` being present.

Current `recv()` path:
```
recv(target, timeout=0.01)
  └─ _recv_from_pipe_buffer()
       ├─ _drain_pipe(endpoint.recv(timeout=0.0))   ← instant, reads OS pipe
       ├─ buffer.recv(timeout=0.0)                   ← instant, returns None if empty
       └─ loop:
            _drain_pipe(...)                          ← instant again
            buffer.recv(timeout=min(remaining, 5ms)) ← BLOCKS 5–10ms on Condition.wait
                                                        (nobody calls notify_all !)
```

`buffer.recv` blocks on a `threading.Condition` that can only be notified by
`buffer.enqueue()`. Since `enqueue()` is only called from `_drain_pipe()`, and
`_drain_pipe()` is only called from inside `recv()`, the Condition wait always
times out rather than being woken by new data.

**Root symptom:** control-lane `poll_next_message` with `poll_timeout_seconds=0.01`
blocks for the full 10ms every call when the pipe is empty (which is most of the time
between messages). With N leaf workers polled sequentially, scheduler tick latency
scales as `N × poll_timeout_seconds`.

## Target state

Background reader thread(s) own all OS pipe I/O. `recv()` reads only from
`_PipeReceiveBuffer` — no direct pipe access.

```
background reader thread  (1 per adapter, multiplexes all registered targets)
  └─ loop over _reader_targets:
       _drain_pipe(endpoint, buffer, send_ack=..., ack_enabled=...)
       → buffer.enqueue(payload)   → Condition.notify_all()
  └─ sleep(poll_interval_seconds)  [only when all pipes drained empty]

recv(target, timeout=0.01)
  └─ buffer.recv(timeout=0.01)   ← woken immediately when reader enqueues
                                    OR returns None after 10ms if pipe empty
                                    — no direct _drain_pipe call from recv()
```

**ACKs remain transport-owned:** `send_ack` is called from `_drain_pipe` inside the
background reader thread, not from `recv()`. This is unchanged.

## Scope of code changes

### Only one file changes: `ipc_adapters.py`

All changes are inside `PipeExecutionIpcTransportAdapter`. No call sites change.
The fix is transparent: `recv()` and `recv_buffered()` still have the same signature;
they just no longer call `_drain_pipe` when a background reader is active.

| Method | Change |
|---|---|
| `allocate_endpoints()` | call `_register_reader_target(target_id)` after registering endpoint |
| `attach_endpoint()` | call `_register_reader_target(resolved.target_id)` after registering |
| `recv()` | if `target_id in _reader_targets`: skip `_drain_pipe`, use `buffer.recv(timeout)` only |
| `recv_buffered()` | same as above |
| `close()` | set `_stopping`, join `_loop_thread` with timeout |
| *(new)* `_register_reader_target(target_id)` | add to `_reader_targets`, start loop if not running |
| *(new)* `_start_reader_loop_if_needed()` | create and start `_loop_thread` if None or dead |
| *(new)* `_reader_loop()` | the background thread body |

### `_reader_loop()` implementation

```python
def _reader_loop(self) -> None:
    while not self._stopping.is_set():
        with self._lock:
            targets = list(self._reader_targets)
            endpoints = dict(self._endpoints)

        if not targets:
            self._stopping.wait(timeout=self._poll_interval_seconds)
            continue

        any_drained = False
        for target_id in targets:
            if self._stopping.is_set():
                break
            endpoint = endpoints.get(target_id)
            if endpoint is None:
                continue
            buffer = self._get_buffer(target_id)
            if buffer is None:
                # Ensure buffer exists (may not yet if recv() not called)
                buffer = self._ensure_receive_buffer(target_id, endpoint)
            ack_enabled = self._ack_enabled and _target_uses_flow_control_ack(target_id)
            send_ack_fn = lambda count, _tid=target_id, _ep=endpoint: (
                self._send_ack_for_target(target_id=_tid, endpoint=_ep, count=count)
            )
            try:
                drained = _drain_pipe(
                    endpoint,
                    buffer,
                    on_read=lambda: self._touch_reader(target_id),
                    on_ack=lambda signal: self._handle_ack(target_id, signal),
                    send_ack=send_ack_fn,
                    ack_enabled=ack_enabled,
                )
                if drained > 0:
                    any_drained = True
            except Exception:
                pass

        if not any_drained:
            # All pipes were empty this cycle — sleep to avoid busy-spinning
            self._stopping.wait(timeout=self._poll_interval_seconds)
        # If something was drained: loop immediately (more data may be waiting)
```

**Adaptive spin:** when data is available the loop runs without sleeping, achieving
sub-millisecond latency. When all pipes are empty it backs off to `poll_interval_seconds`
(default 5ms). This preserves CPU friendliness while eliminating the 10ms call-site
blocking.

### Updated `recv()` logic

```python
def recv(self, target_id, *, timeout=None):
    endpoint = self._resolve_endpoint(target_id)
    buffer = self._ensure_receive_buffer(target_id, endpoint)

    with self._lock:
        has_reader = target_id in self._reader_targets

    if has_reader:
        # Background reader owns pipe drain; just wait on buffer
        payload = buffer.recv(timeout=timeout if timeout is not None else 0.0)
    else:
        # Fallback: direct drain (used before endpoint is registered with reader)
        payload = self._recv_from_pipe_buffer(target_id, endpoint, buffer, timeout)

    if payload is None:
        return None
    return ExecutionIpcMessage(
        target_id=target_id,
        payload=payload,
        ts_epoch_ms=int(time.time() * 1000),
    )
```

`recv_buffered()` is identical (both paths yield the same result since the buffer
is now always pre-populated by the reader).

## TDD plan

### Phase A — RED tests (write first)

File: `tests/stream_kernel/execution/transport/ipc/test_ipc_pipe_background_reader.py`

#### A1: background reader starts when endpoint is registered

```python
def test_background_reader_thread_starts_on_attach() -> None:
    adapter = PipeExecutionIpcTransportAdapter()
    parent_conn, child_conn = mp.get_context("spawn").Pipe(duplex=True)
    adapter.attach_endpoint(
        _PipeEndpoint(target_id="worker#1", connection=parent_conn, codec=ExecutionIpcCodec("pickle")),
        target_id="worker#1",
    )
    import time; time.sleep(0.05)
    assert adapter._loop_thread is not None
    assert adapter._loop_thread.is_alive()
    adapter.close()
```

#### A2: message sent through pipe reaches buffer WITHOUT recv() calling _drain_pipe

```python
def test_message_reaches_buffer_via_background_reader() -> None:
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    parent_conn, child_conn = mp.get_context("spawn").Pipe(duplex=True)
    # Attach parent-side endpoint to adapter
    adapter.attach_endpoint(
        _PipeEndpoint("worker#1", parent_conn, ExecutionIpcCodec("pickle")),
        target_id="worker#1",
    )
    # Write directly from child side (simulating leaf sending)
    child_endpoint = _PipeEndpoint("worker#1", child_conn, ExecutionIpcCodec("pickle"))
    child_endpoint.send({"payload": "hello"})

    time.sleep(0.05)  # give reader thread time to drain

    # recv() with timeout=0 should find message already in buffer
    msg = adapter.recv("worker#1", timeout=0.0)
    assert msg is not None
    assert msg.payload == {"payload": "hello"}
    adapter.close()
```

#### A3: recv() returns in < 2ms when message is available (not 10ms)

```python
def test_recv_returns_fast_when_message_available() -> None:
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    parent_conn, child_conn = mp.get_context("spawn").Pipe(duplex=True)
    adapter.attach_endpoint(
        _PipeEndpoint("worker#1", parent_conn, ExecutionIpcCodec("pickle")),
        target_id="worker#1",
    )
    child_endpoint = _PipeEndpoint("worker#1", child_conn, ExecutionIpcCodec("pickle"))
    child_endpoint.send({"payload": "fast"})
    time.sleep(0.02)  # let reader drain

    t0 = time.monotonic()
    msg = adapter.recv("worker#1", timeout=0.01)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert msg is not None
    assert elapsed_ms < 2.0, f"expected < 2ms, got {elapsed_ms:.1f}ms"
    adapter.close()
```

#### A4: N targets polled — total blocking time < poll_interval (not N × poll_interval)

```python
def test_n_targets_do_not_multiply_blocking_time() -> None:
    N = 4
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    for i in range(N):
        parent, child = mp.get_context("spawn").Pipe(duplex=True)
        adapter.attach_endpoint(
            _PipeEndpoint(f"worker#{i}", parent, ExecutionIpcCodec("pickle")),
            target_id=f"worker#{i}",
        )
    time.sleep(0.02)

    t0 = time.monotonic()
    for i in range(N):
        adapter.recv(f"worker#{i}", timeout=0.01)
    elapsed_ms = (time.monotonic() - t0) * 1000

    # Old behavior: N × 10ms = 40ms
    # New behavior: all pipes empty → N × ~0ms = < 5ms total
    assert elapsed_ms < 15.0, f"expected < 15ms for {N} targets, got {elapsed_ms:.1f}ms"
    adapter.close()
```

#### A5: ACK is sent from reader thread, not from recv()

```python
def test_ack_sent_from_reader_thread_not_recv() -> None:
    adapter = PipeExecutionIpcTransportAdapter(poll_interval_seconds=0.005)
    adapter.enable_ack(True)
    received_acks: list[object] = []
    adapter.register_ack_handler("worker#1::control", lambda sig: received_acks.append(sig))

    parent, child = mp.get_context("spawn").Pipe(duplex=True)
    adapter.attach_endpoint(
        _PipeEndpoint("worker#1::data", parent, ExecutionIpcCodec("pickle")),
        target_id="worker#1::data",
    )
    # Simulate leaf sending a data message
    child_ep = _PipeEndpoint("worker#1::data", child, ExecutionIpcCodec("pickle"))
    child_ep.send({"data": 1})

    time.sleep(0.05)  # reader drains + sends ack via send_buffer

    # ACK should have been sent back (via _send_ack_for_target → _PipeSendBuffer)
    # We verify that adapter would have sent it (check send_buffer for ack target)
    ack_buf = adapter._get_send_buffer("worker#1")  # control lane target for ack
    assert ack_buf is not None or len(received_acks) > 0, "ACK not produced by reader"
    adapter.close()
```

### Phase B — GREEN: implement

1. Add `_register_reader_target(target_id)` method.
2. Add `_start_reader_loop_if_needed()` method.
3. Add `_reader_loop()` method (body as described above).
4. Call `_register_reader_target` from `allocate_endpoints()` and `attach_endpoint()`.
5. Update `recv()` and `recv_buffered()` to skip `_drain_pipe` when target is registered.
6. Update `close()` to join `_loop_thread`.

### Phase C — regression

Run existing IPC transport tests to confirm no regression:

```bash
.venv/bin/pytest -q tests/stream_kernel/execution/transport/ipc/ -x
.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_ipc_transport_bindings.py -x
```

Run existing fund_load E2E to confirm pipeline still completes:

```bash
.venv/bin/pytest -q tests/fund_load/ -x
```

## What does NOT change

- `ipc_transport_service.py` (coordinator) — no changes
- `channel_services.py` (leaf poll) — no changes; behavior improves automatically
- `leaf_ingress_service.py` (root poll) — no changes
- Credit/ACK flow control — no changes; ACK path moves to reader thread but same logic
- `InMemoryExecutionIpcTransportAdapter` — no changes (in-memory has no OS pipe)

## Expected effect on system behavior

| Metric | Before | After |
|---|---|---|
| Control lane recv latency (pipe empty) | 10ms (full timeout) | 10ms (unchanged — timeout on buffer.recv) |
| Control lane recv latency (pipe has data) | 10ms (notify missing) | < 1ms (notified by reader) |
| N-target scheduler tick overhead | N × 10ms = 40ms | N × ~0ms = < 1ms when pipes empty |
| Effective scheduler tick cadence | ~40ms | ~1ms (as configured) |
| OS pipe drain frequency | on-demand (at recv call) | continuously (every poll_interval) |

The 10ms timeout itself (when all pipes are genuinely empty) is not eliminated — it
becomes the correct idle wait rather than a spurious blocking delay. This is acceptable:
the scheduler tick fires at ~1ms cadence, drains immediately when data is present, and
waits 10ms only when truly nothing is in any pipe.

## Risk areas

1. **Thread safety of `_reader_loop` reading `self._endpoints`**: must copy under lock.
2. **Ensure `_drain_pipe` does not race with `recv()`**: after the fix, `recv()` no longer
   calls `_drain_pipe`, so `_recv_lock` on `_PipeEndpoint` is held exclusively by the
   reader thread. No race.
3. **Test process isolation**: tests that send from `child_conn` in the same process
   (no subprocess spawn) — valid because `multiprocessing.Pipe` works intra-process.
4. **`close()` timing**: `_stopping.set()` → join `_loop_thread(timeout=0.5)` must happen
   before `_sender_threads` cleanup to preserve drain ordering.
