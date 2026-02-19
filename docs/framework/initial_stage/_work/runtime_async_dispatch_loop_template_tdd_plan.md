# Runtime async dispatch loop template (TDD plan)

## Goal

Define one reusable platform pattern for non-blocking sink execution:

- queue-based handoff from sync caller to async worker loop;
- deterministic drain on shutdown;
- bounded queue with explicit overflow policy;
- reusable for tracing, logging, and outbound API wrappers.

This subplan is part of:

- [runtime_ipc_bytes_and_nonblocking_observability_tdd_plan](runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md)

## Contract

`AsyncDispatchLoop` must provide:

- `start()`
- `submit(item) -> bool`
- `drain(timeout_seconds) -> bool`
- `stop(drain: bool, timeout_seconds: float) -> None`
- `metrics() -> dict[str, int]`

Execution contract:

- hot path (`submit`) must not execute sink I/O inline;
- stop with `drain=True` must flush queued work before shutdown;
- when queue is full, overflow is explicit (`submit=False`, dropped counter increment).

## TDD steps

### Step A — loop primitive contract

RED tests:

- items submitted are processed in-order;
- `drain()` waits for queued items;
- `submit()` after stop returns `False`.

GREEN:

- add runtime service helper `AsyncDispatchLoop`.

### Step B — supervisor tracing integration

RED tests:

- supervisor trace dispatch does not call `asyncio.run` per record;
- tracing still persists records and hop events with existing contract.

GREEN:

- route supervisor trace emission through `AsyncDispatchLoop`;
- flush path drains async loop before sink flush/close.

### Step C — lifecycle logging migration (next)

- use the same loop template for lifecycle sink fanout;
- keep log format unchanged.

### Step D — outbound API wrappers (next)

- async backends awaited natively;
- sync backends offloaded via `to_thread` under same queue/drain policy.

## Status

- [x] Step A completed.
- [x] Step B completed.
- [ ] Step C pending.
- [ ] Step D pending.

## Current delta (2026-02-19)

- Added reusable runtime component:
  - `src/stream_kernel/platform/services/runtime/async_dispatch_loop.py`
- Supervisor tracing now dispatches through async loop instead of per-record `asyncio.run(...)`.
- Flush/close path drains dispatch queue before sink flush/close.
