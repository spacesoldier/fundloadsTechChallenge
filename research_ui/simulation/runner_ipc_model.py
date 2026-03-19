"""
Runner + IPC transport simulation (asyncio-native).

Models the real execution topology faithfully:
  source (root) → send_queue → sender_thread → OS pipe → reader_loop
    → ipc_recv_buffer → scheduler_pump (tick) → runner_queue → runner_loop

Covers:
  * How runner queue depth behaves under various load/latency ratios
  * How scheduler tick interval and drain_budget control IPC→runner delivery
  * How a slow node starves the tick and creates IPC backlog
  * Root vs leaf role differences

Run:
    python runner_ipc_model.py
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SimConfig:
    # Runner
    node_latency_ms: float = 1.0       # average time to execute one node
    node_latency_jitter: float = 0.0   # ±jitter (ms); 0 = deterministic

    # Scheduler pump (system.scheduler.tick)
    tick_interval_ms: float = 5.0      # nominal tick interval
    drain_budget: int = 32             # max messages drained per tick per lane

    # IPC pipe
    pipe_capacity: int = 512           # OS pipe buffer depth (messages)
    reader_poll_ms: float = 5.0        # background reader poll interval
    send_latency_ms: float = 0.05      # per-message encode + os.write latency

    # Source (root side)
    message_rate_per_sec: float = 100.0

    # Simulation
    duration_s: float = 3.0
    warmup_s: float = 0.2              # messages during warmup excluded from stats

    # Optional second "slow" node in the chain (e.g. OTLP trace emit)
    slow_node: bool = False
    slow_node_latency_ms: float = 50.0


# ---------------------------------------------------------------------------
# Instrumented queue — records depth time-series
# ---------------------------------------------------------------------------

class _IQueue:
    """asyncio.Queue wrapper that records depth samples."""

    def __init__(self, name: str, maxsize: int = 0) -> None:
        self.name = name
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.depth_samples: list[tuple[float, int]] = []
        self.total_put: int = 0
        self.total_get: int = 0
        self.dropped: int = 0

    # ---- producers --------------------------------------------------------

    async def put(self, item: object) -> None:
        await self._q.put(item)
        self.total_put += 1
        self._sample()

    def put_nowait(self, item: object) -> None:
        self._q.put_nowait(item)
        self.total_put += 1
        self._sample()

    def try_put(self, item: object) -> bool:
        """Non-blocking put; drops and returns False if full."""
        try:
            self._q.put_nowait(item)
            self.total_put += 1
            self._sample()
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    # ---- consumers --------------------------------------------------------

    async def get(self) -> object:
        item = await self._q.get()
        self.total_get += 1
        self._sample()
        return item

    def get_nowait(self) -> object:
        item = self._q.get_nowait()
        self.total_get += 1
        self._sample()
        return item

    # ---- introspection ----------------------------------------------------

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()

    def full(self) -> bool:
        return self._q.full()

    def _sample(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            t = loop.time()
        except RuntimeError:
            t = time.monotonic()
        self.depth_samples.append((t, self._q.qsize()))

    def max_depth(self) -> int:
        if not self.depth_samples:
            return 0
        return max(d for _, d in self.depth_samples)

    def mean_depth(self) -> float:
        if not self.depth_samples:
            return 0.0
        return statistics.mean(d for _, d in self.depth_samples)


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass
class _Msg:
    id: int
    created_at: float
    queued_at: Optional[float] = None      # entered runner_queue
    completed_at: Optional[float] = None


# ---------------------------------------------------------------------------
# IPC Pipe Simulation
# ---------------------------------------------------------------------------

class IpcPipeSim:
    """
    Represents one root→leaf (or leaf→root) IPC pipe.

    Layers:
      1. send_queue  — caller puts here (non-blocking)
      2. os_pipe     — background sender drains here (bounded = OS buffer)
      3. (reader on the other end drains os_pipe into recv_buffer)
    """

    def __init__(self, name: str, cfg: SimConfig) -> None:
        self.name = name
        self.cfg = cfg
        self.send_queue = _IQueue(f"{name}/send_q")
        self.os_pipe = _IQueue(f"{name}/os_pipe", maxsize=cfg.pipe_capacity)

    async def sender_thread(self, stop: asyncio.Event) -> None:
        """Background sender: send_queue → os_pipe (with write latency)."""
        while True:
            if stop.is_set() and self.send_queue.empty():
                break
            if self.send_queue.empty():
                await asyncio.sleep(self.cfg.send_latency_ms / 1000.0)
                continue
            try:
                msg = self.send_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.0)
                continue
            await asyncio.sleep(self.cfg.send_latency_ms / 1000.0)  # encode + write
            await self.os_pipe.put(msg)   # blocks if OS pipe buffer full

    async def reader_loop(self, recv_buffer: _IQueue, stop: asyncio.Event) -> None:
        """Background reader thread: os_pipe → process recv_buffer."""
        while True:
            drained = 0
            while not self.os_pipe.empty():
                try:
                    msg = self.os_pipe.get_nowait()
                    await recv_buffer.put(msg)
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if stop.is_set() and self.os_pipe.empty():
                break
            if drained == 0:
                await asyncio.sleep(self.cfg.reader_poll_ms / 1000.0)
            else:
                await asyncio.sleep(0.0)  # yield but stay hot


# ---------------------------------------------------------------------------
# Process simulation (runner + scheduler pump)
# ---------------------------------------------------------------------------

class ProcessSim:
    """
    Simulates one OS process running an AsyncRunner with a scheduler pump.

    Queue chain:
      ipc_recv_buffer → (scheduler_pump every tick_interval_ms) → runner_queue
                                                                       ↓
                                                               runner_loop (node exec)
    """

    def __init__(self, name: str, cfg: SimConfig, role: str = "leaf") -> None:
        self.name = name
        self.cfg = cfg
        self.role = role  # "root" | "leaf"
        self.ipc_recv_buffer = _IQueue(f"{name}/ipc_recv")
        self.runner_queue = _IQueue(f"{name}/runner_q")
        self.processed: list[_Msg] = []
        self.tick_actual_intervals_ms: list[float] = []
        self._tick_count: int = 0

    async def scheduler_pump(self, stop: asyncio.Event) -> None:
        """
        system.scheduler.tick equivalent.
        Fires every tick_interval_ms, drains up to drain_budget items from
        ipc_recv_buffer into runner_queue.

        Key insight: in the real system, if the runner loop holds the event
        loop for a long time, the tick is delayed — this is naturally modeled
        here because asyncio is cooperative.
        """
        last_tick = asyncio.get_event_loop().time()
        while True:
            await asyncio.sleep(self.cfg.tick_interval_ms / 1000.0)

            now = asyncio.get_event_loop().time()
            actual_interval_ms = (now - last_tick) * 1000.0
            self.tick_actual_intervals_ms.append(actual_interval_ms)
            last_tick = now
            self._tick_count += 1

            drained = 0
            while drained < self.cfg.drain_budget and not self.ipc_recv_buffer.empty():
                try:
                    msg = self.ipc_recv_buffer.get_nowait()
                    msg.queued_at = asyncio.get_event_loop().time()
                    await self.runner_queue.put(msg)
                    drained += 1
                except asyncio.QueueEmpty:
                    break

            if stop.is_set() and self.ipc_recv_buffer.empty():
                break

    async def runner_loop(self, stop: asyncio.Event) -> None:
        """
        AsyncRunner.run_async() equivalent.
        Drains runner_queue, executes nodes with simulated latency.

        Two blocking modes:
          slow_node=False  → await asyncio.sleep()  — yields, tick fires normally
          slow_node=True   → time.sleep()            — BLOCKS event loop, tick starved
        """
        while True:
            if stop.is_set() and self.runner_queue.empty():
                break
            if self.runner_queue.empty():
                await asyncio.sleep(0.001)
                continue
            try:
                msg = self.runner_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.0)
                continue

            # Simulate node execution (always async — yields event loop)
            lat = self.cfg.node_latency_ms
            if self.cfg.node_latency_jitter > 0:
                import random
                lat = max(0.0, lat + random.gauss(0, self.cfg.node_latency_jitter))
            await asyncio.sleep(lat / 1000.0)

            # Simulate optional slow work.
            # slow_node=True uses time.sleep (SYNC) — blocks the event loop,
            # preventing tick from firing → models debug serialize / OTLP blocking.
            if self.cfg.slow_node:
                time.sleep(self.cfg.slow_node_latency_ms / 1000.0)  # NO await → blocks

            msg.completed_at = asyncio.get_event_loop().time()
            self.processed.append(msg)


# ---------------------------------------------------------------------------
# Source (root side emitter)
# ---------------------------------------------------------------------------

async def source_loop(
    pipe: IpcPipeSim,
    cfg: SimConfig,
    stop: asyncio.Event,
) -> None:
    """Generates messages at cfg.message_rate_per_sec via IPC send path."""
    interval_s = 1.0 / cfg.message_rate_per_sec
    msg_id = 0
    t0 = asyncio.get_event_loop().time()
    while not stop.is_set():
        now = asyncio.get_event_loop().time()
        msg = _Msg(id=msg_id, created_at=now)
        msg_id += 1
        pipe.send_queue.try_put(msg)
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


def print_report(label: str, cfg: SimConfig, leaf: ProcessSim, pipe: IpcPipeSim) -> None:
    warmup = cfg.warmup_s
    t0 = leaf.processed[0].created_at if leaf.processed else 0.0

    msgs = [m for m in leaf.processed if (m.created_at - t0) >= warmup]
    duration = cfg.duration_s - warmup

    print()
    print("=" * 64)
    print(f"  {label}")
    print("=" * 64)
    print(
        f"  Config: rate={cfg.message_rate_per_sec:.0f}/s  "
        f"node={cfg.node_latency_ms:.1f}ms  "
        f"tick={cfg.tick_interval_ms:.1f}ms  "
        f"drain={cfg.drain_budget}  "
        f"slow={'yes ' + str(cfg.slow_node_latency_ms) + 'ms' if cfg.slow_node else 'no'}"
    )
    print()

    # Throughput
    if msgs:
        throughput = len(msgs) / duration
        print(f"  Throughput:        {throughput:.1f} msg/s  ({len(msgs)} msgs in {duration:.1f}s)")
    else:
        print("  Throughput:        0 (no messages completed after warmup)")

    # E2E latency
    if msgs and all(m.completed_at for m in msgs):
        e2e = [(m.completed_at - m.created_at) * 1000 for m in msgs]
        print(f"  E2E latency P50:   {_pct(e2e, 0.50):.1f}ms")
        print(f"  E2E latency P95:   {_pct(e2e, 0.95):.1f}ms")
        print(f"  E2E latency P99:   {_pct(e2e, 0.99):.1f}ms")

    # Queue-wait time (ipc_recv → runner_queue → dequeued)
    if msgs and all(m.queued_at for m in msgs):
        qwait = [(m.queued_at - m.created_at) * 1000 for m in msgs]
        print(f"  IPC→queue wait P50:{_pct(qwait, 0.50):.1f}ms")
        print(f"  IPC→queue wait P99:{_pct(qwait, 0.99):.1f}ms")

    # Tick fidelity
    ticks = leaf.tick_actual_intervals_ms
    if ticks:
        print(f"  Tick target:       {cfg.tick_interval_ms:.1f}ms")
        print(f"  Tick actual P50:   {_pct(ticks, 0.50):.2f}ms")
        print(f"  Tick actual P99:   {_pct(ticks, 0.99):.2f}ms")
        print(f"  Ticks fired:       {leaf._tick_count}")

    # Queue depths at end of sim
    print()
    print("  Queue depths (max over run | final):")
    for q in [pipe.send_queue, pipe.os_pipe, leaf.ipc_recv_buffer, leaf.runner_queue]:
        print(
            f"    {q.name:<28}  max={q.max_depth():<5}  "
            f"mean={q.mean_depth():.1f}  final={q.qsize()}"
        )

    print(f"  Sent: {pipe.send_queue.total_put}  "
          f"Piped: {pipe.os_pipe.total_put}  "
          f"Received: {leaf.ipc_recv_buffer.total_put}  "
          f"Processed: {leaf.runner_queue.total_get}")
    if pipe.send_queue.dropped:
        print(f"  *** DROPPED at send_queue: {pipe.send_queue.dropped} ***")


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

async def run_scenario(label: str, cfg: SimConfig) -> None:
    stop = asyncio.Event()
    leaf = ProcessSim("leaf#1", cfg, role="leaf")
    pipe = IpcPipeSim("root→leaf#1", cfg)

    tasks = [
        asyncio.create_task(source_loop(pipe, cfg, stop), name="source"),
        asyncio.create_task(pipe.sender_thread(stop), name="sender"),
        asyncio.create_task(pipe.reader_loop(leaf.ipc_recv_buffer, stop), name="reader"),
        asyncio.create_task(leaf.scheduler_pump(stop), name="scheduler"),
        asyncio.create_task(leaf.runner_loop(stop), name="runner"),
    ]

    await asyncio.sleep(cfg.duration_s)
    stop.set()
    # Give tasks a moment to finish draining
    await asyncio.gather(*tasks, return_exceptions=True)

    print_report(label, cfg, leaf, pipe)


# ---------------------------------------------------------------------------
# Root vs Leaf topology (2-process)
# ---------------------------------------------------------------------------

async def run_root_leaf_scenario(label: str, cfg: SimConfig) -> None:
    """
    Root has its own runner processing scheduling/control-plane work.
    Leaf receives business messages from root, executes them.
    Root also receives reply/ack messages from leaf (reverse pipe).
    """
    stop = asyncio.Event()

    root = ProcessSim("root", cfg, role="root")
    leaf = ProcessSim("leaf#1", cfg, role="leaf")

    # root → leaf (business data)
    fwd_pipe = IpcPipeSim("root→leaf", cfg)
    # leaf → root (replies / ack)
    rev_pipe = IpcPipeSim("leaf→root", dataclass_replace(cfg, message_rate_per_sec=cfg.message_rate_per_sec * 0.3))

    tasks = [
        # Root: generates messages, also runs its own runner
        asyncio.create_task(source_loop(fwd_pipe, cfg, stop), name="root.source"),
        asyncio.create_task(fwd_pipe.sender_thread(stop), name="fwd.sender"),
        asyncio.create_task(fwd_pipe.reader_loop(leaf.ipc_recv_buffer, stop), name="fwd.reader"),

        # Leaf: scheduler pump + runner
        asyncio.create_task(leaf.scheduler_pump(stop), name="leaf.scheduler"),
        asyncio.create_task(leaf.runner_loop(stop), name="leaf.runner"),

        # Leaf → root reverse channel (reply path)
        asyncio.create_task(rev_pipe.sender_thread(stop), name="rev.sender"),
        asyncio.create_task(rev_pipe.reader_loop(root.ipc_recv_buffer, stop), name="rev.reader"),

        # Root runner (processes replies from leaf)
        asyncio.create_task(root.scheduler_pump(stop), name="root.scheduler"),
        asyncio.create_task(root.runner_loop(stop), name="root.runner"),
    ]

    # Leaf sends ~30% back to root as replies
    async def leaf_reply_source() -> None:
        while not stop.is_set():
            if not leaf.runner_queue.empty():
                msg = _Msg(id=-1, created_at=asyncio.get_event_loop().time())
                rev_pipe.send_queue.try_put(msg)
            await asyncio.sleep(1.0 / (cfg.message_rate_per_sec * 0.3))

    tasks.append(asyncio.create_task(leaf_reply_source(), name="leaf.reply"))

    await asyncio.sleep(cfg.duration_s)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    print()
    print("=" * 64)
    print(f"  ROOT/LEAF TOPOLOGY: {label}")
    print("=" * 64)
    print(f"  Config: rate={cfg.message_rate_per_sec:.0f}/s  node={cfg.node_latency_ms:.1f}ms  tick={cfg.tick_interval_ms:.1f}ms")
    print()

    for role, proc, pipe in [("LEAF", leaf, fwd_pipe), ("ROOT (replies)", root, rev_pipe)]:
        msgs = proc.processed
        print(f"  [{role}]  processed={len(msgs)}  "
              f"runner_q_max={proc.runner_queue.max_depth()}  "
              f"ipc_recv_max={proc.ipc_recv_buffer.max_depth()}")
        if msgs and all(m.completed_at for m in msgs):
            e2e = [(m.completed_at - m.created_at) * 1000 for m in msgs]
            print(f"    E2E P50={_pct(e2e, 0.50):.1f}ms  P99={_pct(e2e, 0.99):.1f}ms")
        ticks = proc.tick_actual_intervals_ms
        if ticks:
            print(f"    tick P50={_pct(ticks, 0.50):.2f}ms  P99={_pct(ticks, 0.99):.2f}ms  (target={cfg.tick_interval_ms}ms)")


def dataclass_replace(cfg: SimConfig, **overrides: object) -> SimConfig:
    import dataclasses
    return dataclasses.replace(cfg, **overrides)


# ---------------------------------------------------------------------------
# Credit-window flow control simulation
# ---------------------------------------------------------------------------

class CreditWindowSim:
    """
    Models CreditWindowFlowControlPolicy (root side).

    Maintains an in-flight counter. When window is full, messages are parked
    in pending_outbound. ACK from leaf releases credits and drains pending.

    Real system:
      acquire()   → called before send(), blocks if inflight >= window_size
      try_acquire()→ non-blocking version used in _try_send_now
      release()   → called by _on_flow_control_ack when ACK received from leaf
      pending     → _pending_entries: messages queued when try_acquire fails
    """

    def __init__(self, name: str, window_size: int) -> None:
        self.name = name
        self.window_size = window_size
        self._inflight = 0
        self.pending: list[_Msg] = []

        # Statistics
        self.total_acquired = 0
        self.total_released = 0
        self.total_pending_enqueued = 0
        self.total_pending_flushed = 0
        self.inflight_samples: list[tuple[float, int]] = []
        self.pending_samples: list[tuple[float, int]] = []
        self.credit_stalls = 0          # times try_acquire returned False

    def try_send(self, msg: _Msg, pipe: IpcPipeSim) -> bool:
        """Try to send immediately. Returns False → message buffered in pending."""
        if self._inflight < self.window_size:
            self._inflight += 1
            self.total_acquired += 1
            self._sample_inflight()
            pipe.send_queue.try_put(msg)
            return True
        # No credits: buffer to pending (models _enqueue_pending)
        self.pending.append(msg)
        self.total_pending_enqueued += 1
        self.credit_stalls += 1
        self._sample_pending()
        return False

    def release(self, count: int, pipe: IpcPipeSim) -> int:
        """
        Called when ACK received. Releases credits, flushes pending outbound.
        Models _on_flow_control_ack → flow_control.release → _flush_pending.
        """
        self._inflight = max(0, self._inflight - count)
        self.total_released += count
        self._sample_inflight()

        flushed = 0
        while self.pending and self._inflight < self.window_size:
            msg = self.pending.pop(0)
            self._inflight += 1
            self.total_acquired += 1
            pipe.send_queue.try_put(msg)
            flushed += 1

        if flushed:
            self.total_pending_flushed += flushed
            self._sample_pending()
        return flushed

    def utilization(self) -> float:
        return self._inflight / self.window_size if self.window_size > 0 else 0.0

    def max_inflight(self) -> int:
        return max((v for _, v in self.inflight_samples), default=0)

    def max_pending(self) -> int:
        return max((v for _, v in self.pending_samples), default=0)

    def _sample_inflight(self) -> None:
        try:
            t = asyncio.get_running_loop().time()
        except RuntimeError:
            t = time.monotonic()
        self.inflight_samples.append((t, self._inflight))

    def _sample_pending(self) -> None:
        try:
            t = asyncio.get_running_loop().time()
        except RuntimeError:
            t = time.monotonic()
        self.pending_samples.append((t, len(self.pending)))


async def credit_source_loop(
    cw: CreditWindowSim,
    fwd_pipe: IpcPipeSim,
    cfg: SimConfig,
    stop: asyncio.Event,
) -> None:
    """
    Root source: generates messages at cfg.message_rate_per_sec,
    credit-gates each send via CreditWindowSim.try_send().
    """
    interval_s = 1.0 / cfg.message_rate_per_sec
    msg_id = 0
    while not stop.is_set():
        msg = _Msg(id=msg_id, created_at=asyncio.get_event_loop().time())
        msg_id += 1
        cw.try_send(msg, fwd_pipe)
        await asyncio.sleep(interval_s)


async def ack_reader_loop(
    fwd_pipe: IpcPipeSim,
    ack_pipe: IpcPipeSim,
    stop: asyncio.Event,
    poll_ms: float = 5.0,
) -> None:
    """
    Leaf background reader: drains fwd_pipe.os_pipe → leaf recv_buffer.
    Immediately sends ACK back through ack_pipe for each message received.

    Real system: ACK is sent by the reader thread as soon as message is read
    from the OS pipe (not after business processing). This means credit RTT =
    os_pipe transit time + reader poll latency + ack pipe transit time.
    """
    while True:
        drained = 0
        while not fwd_pipe.os_pipe.empty():
            try:
                msg = fwd_pipe.os_pipe.get_nowait()
                # Immediately ACK: send tiny control message back
                ack = _Msg(id=-(msg.id if isinstance(msg, _Msg) else 0),
                           created_at=asyncio.get_event_loop().time())
                ack_pipe.send_queue.try_put(ack)
                drained += 1
            except asyncio.QueueEmpty:
                break
        if stop.is_set() and fwd_pipe.os_pipe.empty():
            break
        if drained == 0:
            await asyncio.sleep(poll_ms / 1000.0)
        else:
            await asyncio.sleep(0.0)


async def ack_receiver_loop(
    cw: CreditWindowSim,
    fwd_pipe: IpcPipeSim,
    ack_pipe: IpcPipeSim,
    stop: asyncio.Event,
    poll_ms: float = 1.0,
) -> None:
    """
    Root ACK handler: drains ack_pipe.os_pipe, releases credits.
    Models _on_flow_control_ack callback registered on the root's reader.
    """
    while True:
        released = 0
        while not ack_pipe.os_pipe.empty():
            try:
                ack_pipe.os_pipe.get_nowait()
                cw.release(1, fwd_pipe)
                released += 1
            except asyncio.QueueEmpty:
                break
        if stop.is_set() and ack_pipe.os_pipe.empty():
            break
        if released == 0:
            await asyncio.sleep(poll_ms / 1000.0)
        else:
            await asyncio.sleep(0.0)


def print_credit_report(
    label: str,
    cfg: SimConfig,
    cw: CreditWindowSim,
    leaf: ProcessSim,
    fwd_pipe: IpcPipeSim,
    ack_pipe: IpcPipeSim,
    duration: float,
) -> None:
    print()
    print("=" * 64)
    print(f"  {label}")
    print("=" * 64)
    print(
        f"  Config: rate={cfg.message_rate_per_sec:.0f}/s  "
        f"node={cfg.node_latency_ms:.1f}ms  "
        f"tick={cfg.tick_interval_ms:.1f}ms  "
        f"window={cw.window_size}  "
        f"reader_poll={cfg.reader_poll_ms:.0f}ms"
    )
    print()

    # Throughput
    processed = leaf.processed
    if processed:
        tp = len(processed) / duration
        print(f"  Throughput:          {tp:.1f} msg/s  ({len(processed)} processed)")
        e2e = [(m.completed_at - m.created_at) * 1000 for m in processed if m.completed_at]
        if e2e:
            print(f"  E2E latency P50:     {_pct(e2e, 0.50):.1f}ms")
            print(f"  E2E latency P99:     {_pct(e2e, 0.99):.1f}ms")
    else:
        print("  Throughput:          0")

    # Credit window
    print()
    print(f"  Credit window size:  {cw.window_size}")
    print(f"  Max in-flight:       {cw.max_inflight()}  "
          f"({100*cw.max_inflight()/cw.window_size:.0f}% of window)")
    print(f"  Credit stalls:       {cw.credit_stalls}  "
          f"(times try_send returned False)")
    print(f"  Max pending_outbound:{cw.max_pending()}")
    print(f"  Acquired/Released:   {cw.total_acquired}/{cw.total_released}")
    if cw.total_pending_enqueued:
        print(f"  Pending enq/flushed: {cw.total_pending_enqueued}/{cw.total_pending_flushed}")

    # ACK pipe
    print()
    print("  ACK pipe (leaf→root):")
    print(f"    ack send_q total:  {ack_pipe.send_queue.total_put}")
    print(f"    ack os_pipe max:   {ack_pipe.os_pipe.max_depth()}")

    # Queue depths
    print()
    print("  Queue depths (max over run | final):")
    for q in [fwd_pipe.send_queue, fwd_pipe.os_pipe, leaf.ipc_recv_buffer, leaf.runner_queue]:
        print(f"    {q.name:<28}  max={q.max_depth():<5}  final={q.qsize()}")


async def run_credit_window_scenario(
    label: str,
    cfg: SimConfig,
    window_size: int,
    ack_reader_poll_ms: float = 5.0,
) -> None:
    """
    Full root→leaf cycle with credit-window flow control and ACK.

    Topology:
      [root source] → CreditWindowSim → fwd_pipe.send_queue
          ↓ sender_thread
      fwd_pipe.os_pipe
          ↓ ack_reader_loop (leaf side, polls at ack_reader_poll_ms)
            → sends ACK into ack_pipe.send_queue
            → delivers msgs to leaf ipc_recv_buffer
          ↓ ack_pipe.sender_thread → ack_pipe.os_pipe
          ↓ ack_receiver_loop (root side)
            → cw.release() → flush pending_outbound → fwd_pipe.send_queue
      leaf.ipc_recv_buffer → scheduler_pump → runner_queue → runner_loop
    """
    stop = asyncio.Event()
    cw = CreditWindowSim(name="root→leaf#1", window_size=window_size)
    leaf = ProcessSim("leaf#1", cfg, role="leaf")

    fwd_pipe = IpcPipeSim("fwd(root→leaf)", cfg)
    # ACK pipe uses the same physical pipe (duplex) but modeled separately.
    # ACK messages are tiny; no credit gating on ACK channel.
    ack_cfg = dataclass_replace(cfg, send_latency_ms=0.02, pipe_capacity=1024)
    ack_pipe = IpcPipeSim("ack(leaf→root)", ack_cfg)

    # Leaf's reader also needs to deliver to leaf ipc_recv_buffer.
    # We combine ack_reader_loop (sends ACK) with delivery to leaf.ipc_recv_buffer.
    async def combined_leaf_reader(stop: asyncio.Event) -> None:
        while True:
            drained = 0
            while not fwd_pipe.os_pipe.empty():
                try:
                    msg = fwd_pipe.os_pipe.get_nowait()
                    # Deliver to leaf for processing
                    await leaf.ipc_recv_buffer.put(msg)
                    # Immediately ACK back to root (transport-level, before runner processes)
                    ack = _Msg(id=-(msg.id if isinstance(msg, _Msg) else 0),
                               created_at=asyncio.get_event_loop().time())
                    ack_pipe.send_queue.try_put(ack)
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if stop.is_set() and fwd_pipe.os_pipe.empty():
                break
            if drained == 0:
                await asyncio.sleep(ack_reader_poll_ms / 1000.0)
            else:
                await asyncio.sleep(0.0)

    tasks = [
        asyncio.create_task(
            credit_source_loop(cw, fwd_pipe, cfg, stop), name="root.source"),
        asyncio.create_task(
            fwd_pipe.sender_thread(stop), name="fwd.sender"),
        asyncio.create_task(
            combined_leaf_reader(stop), name="leaf.reader+ack"),
        asyncio.create_task(
            ack_pipe.sender_thread(stop), name="ack.sender"),
        asyncio.create_task(
            ack_receiver_loop(cw, fwd_pipe, ack_pipe, stop), name="root.ack_recv"),
        asyncio.create_task(
            leaf.scheduler_pump(stop), name="leaf.scheduler"),
        asyncio.create_task(
            leaf.runner_loop(stop), name="leaf.runner"),
    ]

    await asyncio.sleep(cfg.duration_s)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    print_credit_report(label, cfg, cw, leaf, fwd_pipe, ack_pipe,
                        duration=cfg.duration_s - cfg.warmup_s)


# ---------------------------------------------------------------------------
# Main — four scenarios
# ---------------------------------------------------------------------------

async def main() -> None:
    print("\nRunner + IPC transport simulation")
    print("Each scenario: source → OS pipe → background reader → scheduler pump → runner\n")

    # 1. Healthy: throughput < capacity, runner keeps up
    await run_scenario(
        "S1: Healthy  (50/s, node=1ms, tick=5ms)",
        SimConfig(
            message_rate_per_sec=50,
            node_latency_ms=1.0,
            tick_interval_ms=5.0,
            drain_budget=32,
            duration_s=4.0,
        ),
    )

    # 2. Runner overloaded: node too slow, runner_queue fills up
    await run_scenario(
        "S2: Runner overloaded  (200/s, node=8ms, tick=5ms)",
        SimConfig(
            message_rate_per_sec=200,
            node_latency_ms=8.0,
            tick_interval_ms=5.0,
            drain_budget=32,
            duration_s=4.0,
        ),
    )

    # 3. Tick starvation: slow node (OTLP-like) holds event loop, tick delayed
    await run_scenario(
        "S3: Tick starvation  (50/s, slow_node=50ms, tick=5ms)",
        SimConfig(
            message_rate_per_sec=50,
            node_latency_ms=0.5,
            tick_interval_ms=5.0,
            drain_budget=32,
            slow_node=True,
            slow_node_latency_ms=50.0,
            duration_s=4.0,
        ),
    )

    # 4. Drain budget starvation: tick fires but drain_budget too small
    await run_scenario(
        "S4: Drain budget  (300/s, node=0.5ms, tick=5ms, drain=4)",
        SimConfig(
            message_rate_per_sec=300,
            node_latency_ms=0.5,
            tick_interval_ms=5.0,
            drain_budget=4,   # only 4 messages per tick → IPC recv backlog
            duration_s=4.0,
        ),
    )

    # 5. SYNC blocking (cpu work without await — the real stall)
    #    models build_call_payload_fields / serialize_debug_value on hot path
    await run_scenario(
        "S5: SYNC block (50/s, sync_cpu=10ms per msg — blocks event loop)",
        SimConfig(
            message_rate_per_sec=50,
            node_latency_ms=0.1,    # actual async node fast
            tick_interval_ms=5.0,
            drain_budget=32,
            slow_node=True,
            slow_node_latency_ms=10.0,  # time.sleep — NO yield
            duration_s=4.0,
        ),
    )

    # 6. Root vs Leaf topology
    await run_root_leaf_scenario(
        "Healthy (100/s, node=1ms, tick=5ms)",
        SimConfig(
            message_rate_per_sec=100,
            node_latency_ms=1.0,
            tick_interval_ms=5.0,
            drain_budget=32,
            duration_s=4.0,
        ),
    )


async def credit_window_main() -> None:
    print("\n" + "=" * 64)
    print("  CREDIT WINDOW + ACK SCENARIOS")
    print("  Topology: source → credit_gate → fwd_pipe → leaf_reader")
    print("            leaf_reader → ACK → ack_pipe → root_ack_handler")
    print("            root_ack_handler → release credits → flush pending")
    print("=" * 64)

    base = SimConfig(
        message_rate_per_sec=200,
        node_latency_ms=1.0,
        tick_interval_ms=5.0,
        drain_budget=32,
        reader_poll_ms=5.0,
        send_latency_ms=0.05,
        duration_s=4.0,
        warmup_s=0.2,
    )

    # CW-1: Wide window — credits never exhaust, no pending, free throughput
    await run_credit_window_scenario(
        "CW-1: Wide window=64, 200/s — credits never exhaust",
        base, window_size=64, ack_reader_poll_ms=5.0,
    )

    # CW-2: Tight window — credits saturate, pending grows, ACK RTT determines recovery
    await run_credit_window_scenario(
        "CW-2: Tight window=8, 200/s — credit stalls, pending backlog",
        base, window_size=8, ack_reader_poll_ms=5.0,
    )

    # CW-3: Tight window + slow ACK reader — ACK delayed 20ms, credits held longer
    await run_credit_window_scenario(
        "CW-3: window=8, reader_poll=20ms — ACK RTT extended, pending grows more",
        base, window_size=8, ack_reader_poll_ms=20.0,
    )

    # CW-4: Very tight window + slow node — leaf runner slow, ACK sent at read time
    #        so credits released fast (reader acks immediately, runner lag irrelevant)
    await run_credit_window_scenario(
        "CW-4: window=4, node=10ms — slow runner, but ACK is transport-level (fast)",
        dataclass_replace(base, node_latency_ms=10.0),
        window_size=4, ack_reader_poll_ms=5.0,
    )

    # CW-5: Credit window = 1 — extreme: one message in flight at a time
    await run_credit_window_scenario(
        "CW-5: window=1, 200/s — serial: max 1 in-flight (credit RTT = bottleneck)",
        base, window_size=1, ack_reader_poll_ms=5.0,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "credits":
        asyncio.run(credit_window_main())
    else:
        asyncio.run(main())
