# Transport Layer Analysis — IPC Pipes vs ZeroMQ vs TCP vs Redis Streams

**Date:** 2026-03-16
**Context:** Post-experiment reflection following star (Exp A) and ring (Exp B) simulations

---

## 1. What Was Used and Why It Worked

All experiments (A and B) used Python `multiprocessing.Pipe()` — anonymous OS pipes
wrapped in `mp.connection.Connection` objects. The star simulation additionally used
`PipeExecutionIpcTransportAdapter` from `stream_kernel`, which adds a background
reader thread and per-endpoint sender threads on top of the same raw pipes.

The experiments correctly measured the **architectural difference** between star and ring
topologies: whether root sits in the data path or not. That result is transport-agnostic
— it would hold regardless of the underlying IPC mechanism.

---

## 2. Raw Transport Latency

| Transport | Per-message latency | Mechanism |
|-----------|--------------------|-|
| `mp.Pipe` (anonymous pipe) | 1–10 µs | kernel buffer copy, no network stack |
| ZeroMQ IPC (Unix domain socket) | 5–20 µs | framing + Unix socket |
| ZeroMQ TCP loopback (127.0.0.1) | 20–50 µs | full TCP/IP stack |
| Raw TCP loopback + asyncio | 50–100 µs | TCP + StreamReader overhead |

In the ring simulation, per-hop P50 = **4.2–4.8 ms**. This is dominated by
`tick_interval (5ms) + node_latency (1ms)`, not by raw pipe latency. The difference
between pipes (1–10 µs) and TCP (50–100 µs) is ~90 µs — **2% of a 4ms hop**,
below measurement noise. Switching transport would not change any reported latency.

**The tick-based polling model absorbs all transport-level differences.**
Only if ticks were eliminated (event-driven, interrupt-on-receive) would raw
transport speed become visible in measurements.

---

## 3. Where Transport Choice Does Matter

### 3.1 Backpressure model

`mp.Pipe` with `connection.send_bytes()` blocks indefinitely when the OS pipe
buffer (default: 64 KB on Linux) is full. The sender thread stalls. In
`PipeExecutionIpcTransportAdapter`:

```python
# ipc_adapters.py:395 — _sender_loop
endpoint.send(payload)        # = connection.send_bytes() — blocks if buffer full

# ipc_adapters.py:482 — close()
thread.join(timeout=self._close_join_timeout_seconds)  # = 0.1s, then silently returns
# → sender thread left dangling, unsent data silently discarded
```

This is a **silent failure mode**: when a consumer falls behind, the producer's
sender thread blocks forever, and on shutdown data is lost without any exception
or log entry.

ZeroMQ with `HWM` (High-Water Mark) makes backpressure explicit:

```python
# ZeroMQ send with DONTWAIT flag
try:
    socket.send(payload, zmq.DONTWAIT)
except zmq.Again:
    # queue is full — explicit signal, handle or count drop
    drop_counter += 1
```

The failure is an exception, not a silent hang. Drop policy is configurable
per socket (`ZMQ_SNDHWM`, `ZMQ_RCVHWM`). No thread leaks on shutdown.

### 3.2 OBS fan-out architecture

Currently (ring simulation): each leaf opens 3 dedicated unidirectional pipes
to the obs leaf (logs / monitoring / traces). The obs process polls `n_stages × 3`
recv connections every tick.

With ZeroMQ PUB/SUB:

```
Leaf process:  PUSH socket (or PUB socket) → one endpoint
Obs process:   PULL socket (or SUB socket) ← subscribes to all leaves
```

All OBS traffic from all leaves arrives on **one socket** in the obs process.
ZeroMQ multiplexes internally using fair-queuing. The obs process does not need
`n_stages × 3` explicit connections — it subscribes once per lane type, and the
transport layer handles fan-in.

For fan-out FROM obs to integration backends (Jaeger, Redis, Kafka), ZeroMQ
PUB/SUB is also native:

```
Obs process:  PUB socket
Jaeger agent: SUB socket, filter="jaeger"
Redis writer: SUB socket, filter="redis"
Kafka writer: SUB socket, filter="kafka"
```

Each backend is an independent subscriber. Slow subscribers accumulate in their
own socket buffer and are dropped at HWM independently — exactly the bounded-queue
fan-out we implemented manually with asyncio.Queue.

### 3.3 Network transparency

`mp.Pipe` works only between processes on the **same machine**. If leaf processes
were to be distributed across hosts (e.g., leaf:0 on node-A, leaf:1 on node-B),
anonymous pipes cannot be used at all — the entire transport layer must be replaced.

ZeroMQ and TCP are network-transparent. The same code that uses `tcp://127.0.0.1:5555`
on a single machine works as `tcp://node-b.internal:5555` across hosts. The ring
topology's leaf-to-leaf data pipes and leaf-to-obs OBS pipes would naturally extend
to distributed deployment.

---

## 4. Reflection: Star and Ring Topology in Light of Transport Choice

### 4.1 Star topology — transport choice amplifies the root bottleneck

In star topology, root routes **all** cross-process messages: data, OBS events,
and ACKs. At realistic load (A-5: ~38,200 messages/s through root's asyncio loop),
the bottleneck is the Python event loop, not the pipes themselves.

However, the transport layer **shapes how badly** the system fails under overload:

- With `mp.Pipe`: sender threads in stage processes block silently when root falls
  behind. The OS pipe buffer fills, `send_bytes()` stalls, and the process hangs.
  At 1000/s source rate, this leads to OOM as sender queues grow unboundedly.

- With **ZeroMQ**: same topological bottleneck (root asyncio loop), but stages
  would raise `zmq.Again` when root's receive buffer hits HWM. Messages are dropped
  explicitly with a counter. No thread stalls, no OOM — the system degrades to a
  configurable drop rate rather than crashing.

- With **TCP + asyncio**: asyncio's flow control (StreamWriter drain/pause) would
  back-pressure the producer coroutine — explicit awaiting, no silent blocks.

**Conclusion for star topology:** ZeroMQ or TCP would not fix the fundamental
architectural problem (root in the data path), but they would **convert a silent
crash into a measurable drop rate**. The topology remains wrong; the failure mode
becomes observable.

### 4.2 Ring topology — transport choice becomes mostly irrelevant for latency,
but matters for OBS architecture

In ring topology, root carries zero data messages. The per-hop latency (4–5ms)
is dominated by tick_interval + node_latency, not by raw transport speed. Switching
from pipes to ZeroMQ IPC would add ~10 µs per hop — invisible against 4ms.

However, ZeroMQ would meaningfully simplify the OBS subsystem:

**Current (ring B-2, pipes):**
```
leaf:0 ──obs_logs_pipe──────────────────┐
leaf:0 ──obs_mon_pipe───────────────────┤
leaf:0 ──obs_traces_pipe────────────────┤
leaf:1 ──obs_logs_pipe──────────────────┤
leaf:1 ──obs_mon_pipe───────────────────┤  obs leaf
leaf:1 ──obs_traces_pipe────────────────┤  n_stages × 3 poll connections
...                                     │  asyncio.Queue per integration
leaf:4 ──obs_traces_pipe────────────────┘  ThreadPoolExecutor for files
```

**With ZeroMQ PUSH/PULL:**
```
leaf:0 ──PUSH──┐
leaf:1 ──PUSH──┤  PULL → obs receiver (one socket, ZMQ fair-queuing)
...            │         → PUSH to jaeger_agent PULL socket
leaf:4 ──PUSH──┘         → PUSH to redis_writer PULL socket
                         → PUSH to kafka_writer PULL socket
                         → PUSH to file_writer  PULL socket
```

Single socket per role. HWM on each downstream socket defines the drop policy per
backend. No manual asyncio.Queue management. The entire fan-out infrastructure
collapses from ~200 lines of asyncio plumbing to ~20 lines of ZMQ socket setup.

### 4.3 The real question: distributed deployment

Both star and ring topologies were simulated on a single machine. The ring topology's
key advantage — direct leaf-to-leaf pipes — assumes leaves can open direct OS pipes.
This is only possible on the same host.

**If leaves must run on different machines:**

- Ring topology with pipes: **impossible** — pipes don't cross machine boundaries.
- Ring topology with ZeroMQ/TCP: **straightforward** — replace each `mp.Pipe` with
  a ZMQ `PAIR` socket or TCP connection. The leaf-to-leaf latency becomes network
  latency (~0.1–1ms on LAN), but root is still idle on the data plane.
- Star topology with ZeroMQ/TCP: root remains the bottleneck, but now adds network
  round-trips through root for every message. Worse than single-machine star.

The ring topology's architectural advantage **grows** in distributed deployments:
direct leaf-to-leaf TCP connections avoid the star's double network hop through root.

### 4.4 Summary: what transport choice changes and what it doesn't

| Property | `mp.Pipe` | ZeroMQ IPC | ZeroMQ/TCP |
|----------|-----------|------------|------------|
| Per-hop latency (tick=5ms model) | 4ms | 4ms | 4ms |
| Raw transport overhead | 1–10 µs | 5–20 µs | 20–100 µs |
| Backpressure on overload | silent block → OOM | explicit drop (zmq.Again) | explicit (asyncio drain) |
| OBS fan-out complexity | n×3 manual connections | 1 PUSH socket per leaf | 1 socket per leaf |
| Single-machine only | yes | yes (IPC) / no (TCP) | no |
| Distributed deployment | no | yes | yes |
| Star root bottleneck removed | no | no | no |
| Ring advantage preserved | yes | yes | yes |

**The architectural choice (star vs ring) is independent of and more impactful than
the transport choice (pipes vs ZeroMQ vs TCP).** The topology determines whether
the root is in the data path; the transport determines how gracefully the system
fails under load and whether it can span multiple machines.

In a production system that needs to be both correct under overload and
potentially distributed, the right combination is:
**ring topology + ZeroMQ (IPC on one machine, TCP across machines).**

---

## 5. Practical Recommendation for stream_kernel

The current `PipeExecutionIpcTransportAdapter` uses anonymous pipes with blocking
`send_bytes()` in sender threads and a 0.1s join timeout on close. This combination
creates two silent failure modes (sender stall, data loss on shutdown) that become
critical in star topology under realistic OBS load.

**Short term:** add an explicit HWM to sender queues in `_sender_loop` (drop and
count instead of growing unboundedly). This converts OOM into measurable drop rate.

**Medium term:** if ring topology is adopted, the adapter's complexity (multi-endpoint
reader thread, per-endpoint sender threads) is unnecessary for direct leaf-to-leaf
pipes. Each leaf's adapter could be simplified to a direct asyncio-native transport.

**Long term / distributed:** ZeroMQ as the transport layer, with the same ring
topology design. The adapter interface (`send()`, `recv_buffered()`) remains
unchanged; only the underlying Connection objects are replaced with ZMQ sockets.

---

## 6. Redis Streams as Transport

### 6.1 What Redis Streams provide

Redis Streams (`XADD` / `XREAD` / `XREADGROUP`) are an ordered, persistent,
append-only log built into Redis. Key properties relevant to IPC:

| Property | Value |
|----------|-------|
| Delivery guarantee | At-least-once (with consumer groups + XACK) |
| Fan-out | Native: multiple consumer groups on one stream |
| Persistence | Yes — survives process restart, can be replayed |
| Backpressure | Via `MAXLEN` trim (drops old messages); no producer-side block |
| Inspectability | `XLEN`, `XRANGE`, `XPENDING` — lag visible externally |
| Message ordering | Guaranteed within a stream (total order by ID) |

### 6.2 Latency

Every XADD and XREAD is a network round-trip to the Redis server, even on localhost:

| Operation | Latency (localhost Redis) |
|-----------|--------------------------|
| XADD (one message) | 100–300 µs |
| XREAD BLOCK 0 (waiting) | RTT on arrival ≈ 100–300 µs |
| XREADGROUP + XACK | 200–500 µs (two commands) |
| Pipelined batch XADD×N | ~50–100 µs per message at N=100 |

In our ring simulation, per-hop P50 = 4ms (dominated by tick_interval=5ms).
Adding 200–500 µs per message in the data path raises per-hop to ~4.5ms — a
10–12% overhead. Not catastrophic, but meaningful at low node_latency settings.

More importantly: the tick-based polling model **does not apply** to Redis Streams.
With `XREAD BLOCK timeout`, the consumer receives messages as they arrive — the
5ms tick disappears. In a Redis-based pipeline, the scheduling interval is set by
Redis latency itself (~200 µs), not by a timer. Per-hop could drop to **1–2ms**
if node processing is fast, because the `tick_interval` overhead is eliminated.

### 6.3 Redis as the new centralized node — the star topology problem resurfaces

If **all** inter-process traffic (data + OBS + ACKs) goes through Redis Streams,
Redis becomes the single point through which every message passes — structurally
identical to the star topology's root process:

```
Star topology:      source → root (asyncio) → leaf:i → root → sink
Redis-as-transport: source → Redis server   → leaf:i → Redis → sink
```

At A-5 load (200/s, 5 stages, 12 OBS events/msg):
- Data writes to Redis:   5 stages × 1 data × 200/s          =  1,000 XADD/s
- OBS writes to Redis:    5 stages × 12 OBS × 200/s          = 12,000 XADD/s
- ACK writes to Redis:    5 stages × 13 ACK × 200/s          = 13,000 XADD/s (if modelled)
- Total Redis commands:                                       ≈ 26,000 ops/s

Redis single-instance throughput is ~500,000 ops/s — so 26,000 ops/s is
**5% of Redis capacity**. Redis itself would not be the bottleneck at this load.

However at B-3 load (1000/s, 5 stages, 12 OBS/msg):
- Total Redis commands: ≈ 130,000 ops/s — still within Redis capacity (26%).

This is fundamentally different from the Python asyncio loop bottleneck in star
topology, which saturated at ~38,200 messages/s (a hard single-threaded Python limit).
Redis is a C process with I/O multiplexing — its throughput ceiling is 10–20× higher.

**Key distinction:** Redis as transport centralises I/O through an external server
but does not create a single-threaded Python bottleneck. The failure mode under
overload shifts from OOM/silent-hang to Redis backpressure (MAXLEN drops, measurable
consumer lag). This is observable and recoverable.

### 6.4 Fan-out: where Redis Streams genuinely excel

OBS traffic is the dominant load in realistic pipelines (12 events per data message
per stage). With Redis Streams, the fan-out architecture collapses dramatically:

**Current implementation (ring B-2, pipes + asyncio):**
```
leaf:i  →  obs_logs_pipe_i   ─┐
leaf:i  →  obs_mon_pipe_i    ─┤  obs process
leaf:i  →  obs_traces_pipe_i ─┤  (n_stages × 3 recv connections)
...                           │  → asyncio.Queue per integration
                              │  → jaeger_task, redis_task, kafka_task
                              └  → file_task[i][lane] × 2
```
5 stages × 3 lanes = 15 OBS pipes, ~200 lines of asyncio fan-out infrastructure.

**With Redis Streams:**
```
leaf:i  →  XADD stream:obs:logs   ─┐
leaf:i  →  XADD stream:obs:mon    ─┤  Redis server (single point of fan-out)
leaf:i  →  XADD stream:obs:traces ─┤
                                   │
           XREADGROUP group:jaeger ─┘── jaeger_consumer (independent process or task)
           XREADGROUP group:redis  ──── redis_consumer
           XREADGROUP group:kafka  ──── kafka_consumer
           XREADGROUP group:files  ──── file_consumer (one per leaf via XRANGE filter)
```
Each leaf calls XADD — **no obs process at all**. Each integration backend is a
consumer group that reads at its own pace. Redis tracks consumer lag via XPENDING.
If Kafka falls behind, its PEL grows — visible in Redis, doesn't affect Jaeger.

The obs process as a separate dedicated process **disappears**. The fan-out
infrastructure is replaced by Redis consumer group semantics.

### 6.5 Persistence and replay — the unique advantage

IPC pipes, ZeroMQ, and TCP are all **transient** transports: if the receiving
process crashes, unread messages are lost. Redis Streams are **persistent**:

- A crashed obs consumer group resumes from the last ACKed message on restart.
- Post-mortem replay: `XRANGE stream:obs:traces 0 +` reads all trace events since
  the stream was created (up to MAXLEN trim).
- Dead-letter: messages not ACKed for N seconds appear in `XPENDING` and can be
  claimed by a recovery process.

For observability data specifically, persistence is highly desirable: traces and
metrics should survive consumer restarts, be replayable for debugging, and be
inspectable without modifying the producer.

### 6.6 Star and ring topology through the Redis Streams lens

#### Star topology with Redis as transport

Replace the Python root asyncio loop with Redis:

```
source  →  XADD stream:data           (200/s)
root    →  XREAD stream:data           (root now reads from Redis)
        →  XADD stream:stage:{i}:data  (root routes, adds to per-stage stream)
stage:i →  XREAD stream:stage:{i}:data
        →  XADD stream:stage:{i}:result
root    →  XREAD all result streams
        →  XADD stream:sink:data
```

This replaces Python asyncio routing with Redis round-trips. Root's Python code
shrinks to a simple routing loop that reads one stream and writes to another.
The bottleneck shifts from Python's event loop to Redis command throughput.
At 38,200 messages/s (A-5 equivalent), Redis handles this easily.

However: the **topological problem remains**. Every message still goes through
root (now via Redis round-trips instead of asyncio). Two XADD + two XREAD per
hop instead of pipe sends — latency is now ~1ms per hop (Redis RTT × 2) vs
4ms with IPC, but root is still a mandatory waypoint.

The star topology's conceptual flaw (single routing point) is not fixed by
choosing Redis as transport. It merely becomes a faster and more observable flaw.

#### Ring topology with Redis Streams for OBS, IPC for data path

The optimal combination emerging from this analysis:

```
Data plane (ring):  leaf:i ──IPC pipe──▶ leaf:i+1     (4ms per hop, no Redis)
OBS plane:          leaf:i ──XADD──▶ Redis stream:obs  (async, non-blocking)
Command plane:      root   ──XADD/XREAD──▶ Redis stream:ctrl (rare events)
```

- **Data path**: ring topology with direct IPC pipes — 4ms per hop, zero Redis overhead
- **OBS path**: each leaf XADDs directly to Redis streams; no obs process needed;
  integration consumers are independent Redis consumer groups reading at their own rate
- **OBS fan-out**: native Redis consumer groups — Jaeger, Redis metrics, Kafka,
  file consumers each read independently; lag visible externally; replay available
- **Root**: minimal Redis command plane — sends control events to stream:ctrl,
  no data routing at all

Expected per-hop latency: unchanged at 4ms (data path is still IPC).
OBS latency: 200–500 µs per event (XADD to Redis), then consumer-side polling
interval for delivery to Jaeger/Kafka/etc. Typically sub-second for observability.

This hybrid is the practical production design:
**ring IPC for data (low latency, high throughput) + Redis Streams for OBS
(persistence, fan-out, inspectability) + root on Redis command channel (decoupled
from data plane, survives leaf restarts).**

### 6.7 Summary table — all four transport options

```
══════════════════════════════════════════════════════════════════════════════════════
  Transport      │ Per-hop   │ Throughput │ Backpressure  │ Fan-out   │ Persistence
                 │ latency   │ ceiling    │ on overload   │           │
══════════════════════════════════════════════════════════════════════════════════════
  IPC pipes      │  4ms*     │  ~1M msg/s │ silent block  │ manual    │ no
  ZeroMQ IPC     │  4ms*     │  ~1M msg/s │ zmq.Again     │ PUB/SUB   │ no
  ZeroMQ TCP     │  4ms*     │ ~500k msg/s│ zmq.Again     │ PUB/SUB   │ no
  Redis Streams  │  4–5ms†   │ ~500k ops/s│ MAXLEN drop   │ native CG │ yes
══════════════════════════════════════════════════════════════════════════════════════
  * dominated by tick_interval=5ms; raw transport 1–100 µs is invisible
  † if tick-based polling replaced by XREAD BLOCK: could drop to 1–2ms per hop
  CG = consumer groups
```

| Criterion | IPC | ZeroMQ | Redis Streams |
|-----------|-----|--------|---------------|
| Best for data path (latency) | ✓ | ✓ | — |
| Best for OBS fan-out | — | ✓ | ✓✓ |
| Persistence / replay | — | — | ✓✓ |
| Consumer lag visibility | — | — | ✓✓ |
| Distributed deployment | — | ✓ | ✓ |
| Silent failure on overload | ✗ | ✓ | ✓ |
| Eliminates star bottleneck | topology, not transport | topology, not transport | topology, not transport |

**The star vs ring architectural conclusion is transport-agnostic.**
Redis Streams are not a solution to the root bottleneck — they are a better
transport for the OBS plane specifically, with persistence and native fan-out
as unique advantages that no pipe-based transport can provide.

---

## 7. Throughput Analysis

### 7.1 Methodology and assumptions

All numbers below are Python-realistic figures — not C benchmark peaks. They account
for pickle serialization overhead (~200–500 bytes per message for our dataclasses),
Python function call overhead, and GIL contention where relevant. Raw C-level
benchmarks (e.g. official Redis benchmark, ZeroMQ's perf tools) are 2–10× higher
but not achievable through Python client libraries.

Message size assumed: **200–500 bytes** (pickled `PipelineRecord` / `ObsEvent`).

### 7.2 Single-connection throughput ceiling

Maximum sustained messages/s on one logical connection in Python:

```
══════════════════════════════════════════════════════════════════════════════
  Transport                    │  Throughput ceiling  │  Limiting factor
══════════════════════════════════════════════════════════════════════════════
  mp.Pipe (Connection.send)    │   100k – 250k msg/s  │  pickle + write syscall
  mp.Pipe + adapter threads    │    80k – 180k msg/s  │  queue handoff overhead
  ZeroMQ IPC (pyzmq PUSH/PULL) │   200k – 400k msg/s  │  framing; faster pickle path
  ZeroMQ TCP loopback (pyzmq)  │   150k – 300k msg/s  │  TCP stack + framing
  Redis XADD (asyncio, solo)   │    30k –  80k ops/s  │  asyncio ↔ Redis RTT
  Redis XADD (pipeline N=100)  │   200k – 500k ops/s  │  batch RTT amortised
  Redis XREADGROUP (asyncio)   │    20k –  50k ops/s  │  heavier command + ACK
══════════════════════════════════════════════════════════════════════════════
```

Notes:
- `mp.Pipe + adapter threads` is lower than raw `mp.Pipe` due to the extra queue
  handoff between the asyncio loop and the sender/receiver threads in
  `PipeExecutionIpcTransportAdapter`.
- ZeroMQ is faster than raw pipes at the Python level because pyzmq releases the
  GIL during `send()`/`recv()` — multiple ZMQ sockets in one process can operate
  concurrently without GIL contention.
- Redis without pipelining is the slowest because every `XADD` is a full
  asyncio coroutine round-trip (~100–300 µs). Pipelining 100 commands per RTT
  collapses per-command cost to 1–3 µs.

### 7.3 Throughput in the context of our experiment loads

The table below maps measured or estimated message rates from the experiments onto
each transport's ceiling. Cells marked `✓` are within capacity; `~` means near the
ceiling (monitor carefully); `✗` means the transport becomes the bottleneck.

```
══════════════════════════════════════════════════════════════════════════════════════════
  Load scenario           │  Rate      │ mp.Pipe │ ZMQ IPC │ ZMQ TCP │ Redis  │ Redis
                          │  (msg/s)   │ +adapter│         │         │ solo   │ pipeline
══════════════════════════════════════════════════════════════════════════════════════════
  Star A-1 data only      │      600   │  ✓      │  ✓      │  ✓      │  ✓     │  ✓
  Star A-5 total (root)   │   38,200   │  ✓      │  ✓      │  ✓      │  ~     │  ✓
  Ring B-2 data per leaf  │      200   │  ✓      │  ✓      │  ✓      │  ✓     │  ✓
  Ring B-2 OBS (obs leaf) │   11,820   │  ✓      │  ✓      │  ✓      │  ✓     │  ✓
  Ring B-3 data per leaf  │    1,000   │  ✓      │  ✓      │  ✓      │  ✓     │  ✓
  Ring B-3 OBS (obs leaf) │   55,872   │  ✓      │  ✓      │  ✓      │  ✗     │  ~
  Projected 2000/s data   │    2,000   │  ✓      │  ✓      │  ✓      │  ✓     │  ✓
  Projected 2000/s OBS    │  111,744   │  ~      │  ✓      │  ✓      │  ✗     │  ✗
══════════════════════════════════════════════════════════════════════════════════════════
  ceiling (approx)        │            │ 80–180k │200–400k │150–300k │30–80k  │200–500k
══════════════════════════════════════════════════════════════════════════════════════════
  ✓ within capacity   ~ approaching ceiling   ✗ transport is the bottleneck
```

**Reading the table:**

- At **A-5 star load (38,200 msg/s)**, all transports handle the volume. The bottleneck
  is the Python asyncio event loop routing logic — not the pipes. This confirms the
  star topology's problem is architectural (single-threaded Python router), not
  transport-level.

- At **B-3 ring OBS load (55,872 events/s)**, Redis without pipelining hits its ceiling
  (~50k ops/s). This means that if Redis Streams were used as the OBS transport in the
  ring B-3 scenario, the obs leaf would drop ~10% of events at the XADD step without
  explicit pipelining. With pipeline N=50, this clears the ceiling.

- At a projected **2000/s source rate**, the OBS load reaches ~112k events/s. IPC pipes
  approach their ceiling (~80–180k range), ZeroMQ handles it comfortably, and Redis
  without pipelining saturates completely. ZeroMQ IPC becomes the clear winner for OBS
  transport at this scale on a single machine.

### 7.4 Throughput per connection vs total system throughput

The numbers above are per-connection. In the ring topology, OBS load is distributed
across `n_stages × 3` independent connections (one per leaf per lane). The obs leaf
drains all of them sequentially in its tick loop. The per-connection rate is:

```
Ring B-3:  55,872 OBS events/s total  ÷  15 connections  =  3,725 events/s per pipe
```

3,725 events/s per pipe is far below any transport's per-connection ceiling. The
bottleneck at B-3 is **not** the transport per connection — it is the obs leaf's
asyncio tick loop draining 15 connections sequentially at 5ms intervals.

With drain_budget=32 per connection per tick and 5ms ticks (200 ticks/s):
- Max drain rate: 32 × 15 × 200 = 96,000 events/s → sufficient for B-3's 55,872/s.

If Redis Streams were used for OBS (one XADD per event), the leaf does:
- 55,872 XADD/s solo → saturates Redis asyncio client
- But: leaves can pipeline XADDs naturally — drain 32 events from the runner queue,
  then pipeline all 32 XADDs in one Redis round-trip → ~1,750 XADD batches/s, well within pipeline capacity.

### 7.5 Where each transport becomes the bottleneck first

Across all scenarios, the sequence of bottlenecks as load increases:

```
Load (msg/s)   Bottleneck
────────────────────────────────────────────────────────────────────────────────
      200       None (all transports idle)
    1,000       Leaf node_latency (1ms/rec → 1k rec/s per leaf ceiling)
    5,000       Star: Python asyncio routing loop (~38k msg/s including OBS/ACK)
   10,000       Redis solo XADD (asyncio, no pipeline)
   30,000       Star: Python asyncio routing loop fully saturated, OOM risk
   50,000       Ring: obs leaf tick-loop drain budget (at 5ms tick, 32 budget)
   80,000       mp.Pipe + adapter threads (single connection)
  100,000       Redis XADD with pipeline (asyncio overhead)
  200,000       mp.Pipe raw (single connection pickle ceiling)
  300,000       ZeroMQ TCP loopback (pyzmq)
  400,000       ZeroMQ IPC (pyzmq, single connection)
  500,000+      Redis XADD with pipeline (C client / Lua scripts)
────────────────────────────────────────────────────────────────────────────────
```

Key observation: **the star topology's Python asyncio loop saturates at ~38k msg/s**,
which is below the transport ceiling of every option. Switching transport in star
topology cannot push past 38k msg/s — the router code itself is the ceiling.
Only removing root from the data path (ring topology) breaks through this limit.

In ring topology, the next bottleneck after removing root is the **obs leaf drain loop**
(~50k events/s at tick=5ms, drain_budget=32), then the **transport ceiling** itself.
Increasing drain_budget to 128 or reducing tick_interval to 1ms would push the
obs drain ceiling to ~400k events/s — past any transport's Python-level limit.

### 7.6 Throughput scaling: ring vs star as load increases

```
  Source    │  Star E2E      │  Star fate        │  Ring E2E    │  Ring fate
  rate      │  (A-5 config)  │                   │  (B config)  │
────────────┼────────────────┼───────────────────┼──────────────┼────────────
   200/s    │  1,195ms P50   │ overloaded root   │   22ms P50   │ normal
   500/s    │  est. 5,000ms  │ severe queue      │   ~25ms P50  │ normal
  1,000/s   │  OOM → crash   │ silent data loss  │  2,455ms P50 │ leaf:0 queue
  2,000/s   │  (not runnable)│                   │  est. >>5s   │ leaf:0 queue
  5,000/s   │  (not runnable)│                   │  transport   │ pipe ceiling
            │                │                   │  dependent   │ ~80–400k
────────────┴────────────────┴───────────────────┴──────────────┴────────────
```

Star topology crashes before it reaches the transport ceiling. Ring topology's
failure mode at high rate is **leaf queue depth** (graceful backpressure), not a
transport ceiling. The transport ceiling only becomes relevant in ring topology at
rates far above any realistic single-machine workload.

### 7.7 Revised full comparison table

```
════════════════════════════════════════════════════════════════════════════════════════════
  Transport        │ Per-hop  │ Single-conn  │ Backpressure    │ Fan-out  │ Persist │ Dist
                   │ latency* │ throughput   │ on overload     │          │         │ -rib
════════════════════════════════════════════════════════════════════════════════════════════
  mp.Pipe raw      │  4ms     │ 100–250k/s   │ silent block    │ manual   │ no      │ no
  mp.Pipe +adapter │  4ms     │  80–180k/s   │ silent block    │ manual   │ no      │ no
  ZeroMQ IPC       │  4ms     │ 200–400k/s   │ zmq.Again drop  │ PUB/SUB  │ no      │ no
  ZeroMQ TCP       │  4ms     │ 150–300k/s   │ zmq.Again drop  │ PUB/SUB  │ no      │ yes
  Redis solo       │  4–5ms†  │  30– 80k/s   │ MAXLEN drop     │ native   │ yes     │ yes
  Redis pipeline   │  4–5ms†  │ 200–500k/s   │ MAXLEN drop     │ native   │ yes     │ yes
════════════════════════════════════════════════════════════════════════════════════════════
  * dominated by tick_interval=5ms in our model; raw transport 1–500 µs invisible
  † if tick replaced by XREAD BLOCK: per-hop drops to 1–2ms (transport-driven scheduling)
```

**Recommended combination for production:**

| Traffic type | Transport | Reason |
|---|---|---|
| Data pipeline (leaf→leaf) | `mp.Pipe` raw or ZeroMQ IPC | lowest latency, no Redis in hot path |
| OBS events (leaf→obs) | Redis Streams + pipeline | persistence, native fan-out, inspectable lag |
| Command plane (root→leaves) | Redis Streams or ZeroMQ | rare traffic, decoupled from data path |

---

## 8. Final Recommendation: Ring+ZMQ vs Ring+Redis for Data Transport

### 8.1 The question

After establishing that ring topology is architecturally superior to star, a practical
question remains: for the data plane specifically, should leaf-to-leaf pipes be
ZeroMQ sockets or Redis Streams? The answer depends on what "ring+Redis" actually means.

### 8.2 Ring+Redis for data is a re-invention of the star

If all inter-leaf data goes through Redis Streams:

```
leaf:0  →  XADD stream:data:1  →  Redis  →  XREAD  →  leaf:1
leaf:1  →  XADD stream:data:2  →  Redis  →  XREAD  →  leaf:2
...
```

Every data hop passes through Redis. Redis becomes the mandatory waypoint for every
message — structurally equivalent to the star root:

```
Star topology:        leaf:i  →  root (asyncio)  →  leaf:i+1
Ring+Redis data:      leaf:i  →  Redis server    →  leaf:i+1
```

The architectural problem (centralised message routing) is not solved; it is
delegated to an external process. The difference:

| | Star root (Python asyncio) | Redis as relay |
|--|--|--|
| Routing ceiling | ~38k msg/s (GIL-bound) | ~100–200k msg/s (C, I/O-multiplexed) |
| Failure mode | OOM / silent hang | MAXLEN drop (observable) |
| Observability | none (asyncio internals) | XLEN, XPENDING, INFO |
| Added latency per hop | ~0.5ms (tick-driven) | 200–500 µs (XADD + XREAD RTT) |
| Is root still in data path? | **yes** | **yes** |

Redis raises the ceiling and improves the failure mode, but the fundamental
architectural issue — a centralised node in the hot path — persists.

**Verdict:** ring+Redis for data is a better star, not a true ring. Use it only
if you specifically need Redis's persistence or multi-machine fan-out for data
messages themselves, and you have verified the 100–200k msg/s ceiling is sufficient.

### 8.3 True ring+ZMQ: fully peer-to-peer

With ZeroMQ PAIR sockets (or PUSH/PULL), each leaf connects directly to the next:

```
leaf:0  ──ZMQ PUSH──▶  leaf:1  ──ZMQ PUSH──▶  leaf:2  ...
```

No third-party process in the data path. Per-hop adds only ~10–20 µs (ZMQ framing
over Unix domain socket). Root is completely absent from the data plane.

At 5 stages and 1000 msg/s, the per-leaf data rate is 1000 msg/s — one leaf needs
to receive 1000 msg/s and forward 1000 msg/s. ZMQ IPC ceiling: 200–400k msg/s.
The ceiling is never approached at any realistic single-machine workload.

### 8.4 Throughput ceiling comparison for data plane

```
═══════════════════════════════════════════════════════════════════════════════
  Configuration          │  Data path latency  │  Data throughput ceiling
═══════════════════════════════════════════════════════════════════════════════
  Ring + IPC pipes       │  4ms (tick-driven)  │  ~80–180k msg/s per hop
  Ring + ZMQ IPC         │  4ms (tick-driven)  │  ~200–400k msg/s per hop
  Ring + ZMQ TCP         │  4ms (tick-driven)  │  ~150–300k msg/s per hop
  Ring + Redis Streams   │  4–5ms (XREAD RTT)  │  ~100–200k msg/s (Redis C/s)
  Star + Redis Streams   │  4–5ms (XREAD RTT)  │  ~100–200k msg/s (Redis C/s)
═══════════════════════════════════════════════════════════════════════════════
  Leaf node_latency=1ms  →  natural ceiling ≈ 1,000 msg/s per leaf
  Leaf node_latency=0ms  →  ceiling is transport-bound (above table)
═══════════════════════════════════════════════════════════════════════════════
```

When `node_latency > 0`, the leaf's own processing time dominates. At 1ms latency,
a leaf can sustain 1,000 msg/s regardless of which transport it uses — all options
are identical in practice. The transport ceiling only matters when node_latency
approaches zero (i.e., trivially fast business logic).

### 8.5 Recommended split

```
────────────────────────────────────────────────────────────────────────────────
  Traffic type      │  Transport          │  Reason
────────────────────────────────────────────────────────────────────────────────
  Data (leaf→leaf)  │  IPC pipe or        │  Lowest latency; zero external
                    │  ZeroMQ IPC         │  dependency; ceiling ~200–400k/s
                    │                     │  (never reached in practice)
                    │                     │
  OBS (leaf→obs)    │  Redis Streams      │  Persistence, native consumer groups,
                    │  pipelined XADDs    │  fan-out without obs process,
                    │                     │  inspectable lag via XPENDING
                    │                     │
  Command plane     │  Redis Streams or   │  Rare events; decoupled from data;
  (root→leaves)     │  ZeroMQ DEALER/     │  survives leaf restarts when persistent
                    │  ROUTER             │
────────────────────────────────────────────────────────────────────────────────
```

**Choose ZMQ IPC over IPC pipes when:**
- you need explicit backpressure (zmq.Again vs silent block)
- you anticipate multi-machine deployment (just swap ipc:// for tcp://)
- you want PUB/SUB for OBS fan-out without Redis dependency

**Choose Redis Streams for OBS when:**
- traces/logs must survive consumer restarts (replay from persistent log)
- Jaeger, Redis metrics, and Kafka consumers need independent lag tracking
- post-mortem analysis requires querying OBS history externally

**Choose IPC pipes (current implementation) when:**
- single-machine deployment is sufficient
- simplicity over explicit backpressure is acceptable
- no need for OBS persistence or distributed fan-out

---

## 9. Full-Redis Ring: All Traffic Through Redis Streams

### 9.1 What this configuration looks like

"All traffic through Redis" means every plane — data, OBS, and control — uses
Redis Streams as the sole transport:

```
Data plane:     leaf:i  →  XADD stream:data:{i+1}  →  XREAD  →  leaf:i+1
OBS plane:      leaf:i  →  XADD stream:obs:logs    →  XREADGROUP → jaeger_consumer
                leaf:i  →  XADD stream:obs:mon     →  XREADGROUP → redis_consumer
                leaf:i  →  XADD stream:obs:traces  →  XREADGROUP → kafka_consumer
Command plane:  root    →  XADD stream:ctrl        →  XREADGROUP → leaf:i
```

No IPC pipes. No ZeroMQ. No shared memory. All inter-process communication is
mediated by the Redis server.

### 9.2 Architectural analysis

**Advantages:**

- **Fully location-transparent**: leaf processes can run on any machine that
  has network access to Redis. Scaling out to multiple hosts requires no code
  change — just connect to the same Redis server.
- **Uniform observability**: `XLEN`, `XPENDING`, `INFO`, `MONITOR` expose the
  state of every traffic plane in a single tool. Consumer lag is externally
  visible without instrumenting the application.
- **At-least-once delivery with XACK**: data messages can be re-delivered if a
  leaf crashes mid-processing. Star topology (asyncio) and IPC pipes lose in-flight
  messages on crash; Redis retains them in the pending-entry list (PEL).
- **Decoupled restart**: any leaf can restart independently without the root
  noticing. It resumes from the last ACKed message on its consumer group.
- **OBS fan-out without obs process**: eliminates the dedicated obs leaf process entirely.

**Disadvantages and risks:**

1. **Redis is still a centralised relay — structurally the same as star root**

   For the data plane, each hop requires two Redis operations (XADD by sender, XREAD
   by receiver). Redis itself is single-threaded for command processing. At 5 stages
   and 1000 data msg/s with OBS (12 events/msg):

   ```
   Data XADDs:         5 hops × 1,000/s          =    5,000 ops/s
   OBS XADDs:          5 stages × 12 × 1,000/s   =   60,000 ops/s
   Data XREADs:        5 hops × 1,000/s           =    5,000 ops/s
   XACK (data):        5 hops × 1,000/s           =    5,000 ops/s
   ──────────────────────────────────────────────────────────────
   Total Redis ops/s:                              ≈   75,000 ops/s
   ```

   Redis throughput ceiling (localhost, single instance): ~500,000 ops/s (C benchmark),
   ~100,000–200,000 ops/s through Python async client. At 75k ops/s, this is
   **37–75% of the Python client ceiling**. Functional, but with minimal headroom.

   At 5,000 data msg/s (50× our B-3), total ops would reach ~375,000/s — saturating
   the Python client. Beyond that, Redis itself becomes the star-equivalent bottleneck.

2. **Latency doubles per hop**

   Each leaf-to-leaf hop now requires XADD (RTT₁) + XREAD (RTT₂). Even with
   XREAD BLOCK (no polling), minimum per-hop latency is 2× Redis RTT:

   ```
   XADD by leaf:i         →  Redis receives:  ~100–300 µs
   XREAD BLOCK by leaf:i+1 ←  Redis delivers: ~100–300 µs
   Total transport latency: ~200–600 µs per hop
   ```

   Vs IPC pipe: ~1–10 µs. Vs ZMQ IPC: ~5–20 µs. The absolute difference is
   sub-millisecond (0.2–0.6ms), but it compounds with each hop. A 5-stage ring
   accumulates **1–3ms of pure transport overhead** not present in IPC/ZMQ.

3. **Redis single point of failure**

   If the Redis server goes down, all traffic planes — data, OBS, and control —
   stop simultaneously. With IPC/ZMQ, a Redis outage only affects OBS delivery
   (if using the hybrid recommended above). Data processing continues uninterrupted.

4. **Memory growth from PEL (pending-entry list)**

   With XREADGROUP + XACK for at-least-once delivery, each unACKed message sits
   in Redis's PEL. If a consumer falls behind or crashes without ACKing, the PEL
   grows. At 75k ops/s with slow XACK, PEL can consume gigabytes of Redis memory
   rapidly. Requires careful `XAUTOCLAIM` policies and monitoring.

### 9.3 When full-Redis ring makes sense

Despite the trade-offs, full-Redis ring is the right choice in specific scenarios:

| Scenario | Recommendation |
|----------|---------------|
| Multi-machine deployment, no shared filesystem | Full-Redis ring: network transparency is worth the latency overhead |
| Need at-least-once delivery for data messages | Full-Redis ring with XACK |
| Need external replay of data messages for debugging | Full-Redis ring with MAXLEN = replay window |
| Single machine, latency-sensitive, high throughput | Hybrid: IPC/ZMQ data + Redis OBS |
| Single machine, simplicity is paramount | Hybrid or full-Redis depending on volume |

### 9.4 Full-Redis ring throughput limits

```
═══════════════════════════════════════════════════════════════════════════════
  Metric                      │  Value
═══════════════════════════════════════════════════════════════════════════════
  Python Redis client ceiling  │  100–200k ops/s (asyncio, localhost)
  Data+OBS ops at 1,000 msg/s  │  ~75,000 ops/s  (5 stages, 12 OBS/msg)
  Headroom at 1,000/s          │  25–125k ops/s remaining (~25–63%)
  Break-even (Python ceiling)  │  ~1,300–2,600 data msg/s (5 stages, 12 OBS/msg)
  Redis server C ceiling       │  ~500k ops/s (single instance)
  Break-even (C ceiling)       │  ~6,500 data msg/s (5 stages, 12 OBS/msg)
═══════════════════════════════════════════════════════════════════════════════
```

The Python client bottleneck hits first at ~1,300–2,600 data msg/s. Above that,
XADD calls back up behind asyncio concurrency limits. Workarounds:

- **Pipelining**: batch N XADDs per Redis round-trip → raises ceiling to
  ~200–500k ops/s equivalent (matching ZMQ IPC). Adds per-message latency equal
  to the batch window.
- **Multi-instance Redis**: shard streams across multiple Redis servers (data to
  Redis-A, OBS to Redis-B). Doubles the ceiling but adds operational complexity.
- **RedisCluster**: horizontal scaling, but stream fan-out semantics change —
  XREADGROUP across slots requires careful key design.

### 9.5 Comparison: hybrid (IPC data + Redis OBS) vs full-Redis ring

```
═══════════════════════════════════════════════════════════════════════════════════════
  Property                  │  Hybrid (recommended)     │  Full-Redis ring
═══════════════════════════════════════════════════════════════════════════════════════
  Data path latency         │  4ms (tick) / ~10µs raw   │  4ms (tick) / 200–600µs raw
  OBS delivery              │  Redis Streams (persist.)  │  Redis Streams (persist.)
  At-least-once (data)      │  no (pipe: fire-and-forget)│  yes (XACK on data streams)
  Data throughput ceiling   │  80–400k msg/s             │  1,300–2,600 msg/s (py client)
  Redis ops at 1000/s       │  ~60,000 OBS only          │  ~75,000 data+OBS
  Redis as SPOF             │  OBS only (data unaffected)│  all planes halt on crash
  Multi-machine data path   │  no (IPC only)             │  yes (network-transparent)
  Operational complexity    │  medium (two transports)   │  low (one transport)
  Recommended for           │  single-machine production │  multi-machine / at-least-once
═══════════════════════════════════════════════════════════════════════════════════════
```

---

## 10. Message Context and Payload Enrichment

### 10.1 The enrichment model

Messages in the pipeline carry two distinct parts:

```python
@dataclass
class PipelineMessage:
    # Mutable payload: transforms at each stage
    payload: bytes | dict | object

    # Enrichable context: grows and accumulates across hops
    context: dict  # e.g. {"trace_id": ..., "request_id": ...,
                   #        "hop_timings": [...], "aggregates": {...},
                   #        "tags": {...}, "schema_version": "1.0"}
```

The **payload** changes semantically at each stage: raw bytes → parsed record →
enriched record → validated record → transformed output. Each stage replaces or
transforms the payload.

The **context** accumulates: trace IDs are set at ingestion and preserved
throughout; timing metadata is appended at each hop; aggregates (counts, sums,
running statistics) are updated in-place; tags from upstream stages remain visible
to downstream ones.

### 10.2 Effect on message size across hops

If context accumulates N fields per hop over H hops, the message size at hop H is:

```
size(hop=0) = payload_size + context_base_size
size(hop=H) = payload_size + context_base_size + H × Δcontext_per_hop
```

Typical values in a 5-stage pipeline:

```
payload_size:        200–2,000 bytes  (compressed or raw data record)
context_base_size:    50–200 bytes    (trace_id, request_id, schema_version)
Δcontext_per_hop:     30–100 bytes    (timestamp, stage_id, hop_latency_ms, 1–2 aggregates)
H = 5 stages:        + 150–500 bytes  of accumulated context

Total at sink:        400–2,700 bytes  (roughly 2× the original payload)
```

This is usually acceptable. However, if aggregates include per-message arrays
(e.g., `{"all_payloads_seen": [...]}`), the context can grow without bound. The
model must distinguish between **fixed-size context fields** (safe) and
**unbounded accumulations** (require explicit eviction policy).

### 10.3 Transport implications of growing message size

| Transport | Sensitivity to message size growth |
|-----------|-----------------------------------|
| IPC pipe | None within OS pipe buffer (64KB); pickle overhead is linear in size |
| ZeroMQ | None up to HWM; larger messages increase per-send latency proportionally |
| Redis XADD | Each XADD stores the full message in the stream; PEL memory grows; MAXLEN trim policy must account for message size, not just count |

The critical implication for Redis Streams: `MAXLEN N` means "keep the last N
messages". If messages double in size as they traverse the pipeline, the memory
footprint of the stream is not N × fixed_size — it is the sum of the actual
sizes. A MAXLEN of 100,000 messages could consume 40MB or 270MB depending on
which stage's messages are retained. **Size the MAXLEN on bytes, not count.**

Redis 7.x supports `MINID` trimming (by ID, which corresponds to time), but not
direct byte-size trimming. A practical workaround: set `MAXLEN ~` (approximate
trim) with a conservative count, and monitor stream memory with `XLEN` + `DEBUG
OBJECT` size estimation.

### 10.4 Context in the ring simulation model

The current ring simulation (`ring_sim.py`) tracks timing metadata but does not
model context enrichment. To add context growth to the model:

```python
@dataclass
class PipelineRecord:
    record_id: int
    created_ns: int
    stage_exits_ns: list[int]           # timing per hop — already modelled
    context: dict = field(default_factory=dict)   # add this
```

Each leaf adds to `context` before forwarding:

```python
record.context[f"stage_{leaf_idx}"] = {
    "processed_at_ns": time.monotonic_ns(),
    "queue_depth": runner_q.qsize(),
    "agg_sum": leaf_state.running_sum,  # example aggregate
}
```

This would make the pickled record size grow by ~60–100 bytes per hop, which the
simulator would expose via `sys.getsizeof(pickle.dumps(record))` at the sink.
The OBS load model would also change: each `ObsEvent` would carry a snapshot of
the context at the time of emission, increasing the OBS XADD payload size.

### 10.5 Context vs payload: the reference vs copy trade-off

Two implementation strategies for the context aggregate:

**Strategy A — Full copy per hop (current implicit model):**
Each stage receives the full message (payload + context), appends to context,
and sends the enlarged message forward. Context is self-contained in the message.
Advantage: no shared state, trivially serializable, works across machines.
Disadvantage: message size grows linearly with hop count; re-serializing large
contexts at each hop adds CPU cost.

**Strategy B — Reference (pointer to external store):**
Messages carry only `context_ref: str` (a key). Each stage writes its enrichment
directly to a shared store (Redis Hash, Postgres row, shared memory segment).
The message payload remains small across all hops.

```python
@dataclass
class PipelineMessage:
    payload: bytes
    context_ref: str    # "ctx:req:abc123"
    # context is in Redis HSET ctx:req:abc123 {stage_0: ..., stage_1: ...}
```

Advantage: message stays small (no growth per hop); context is independently
queryable; all stages see the full accumulated context without deserializing it.
Disadvantage: every stage read/write to external store (adds latency per hop);
the store becomes a dependency; atomicity of context updates requires care.

**Trade-off summary:**

| Property | Full copy per hop | Reference (external store) |
|----------|-------------------|---------------------------|
| Message size growth | linear (H × Δ) | constant (~50 bytes ref) |
| Per-hop CPU (serialization) | grows with context size | constant |
| External dependency | none | Redis / DB required |
| Context inspectability | in the message | in the store (any consumer) |
| Works across machines | yes (full copy) | yes (shared store) |
| Atomicity of context update | N/A (immutable copy) | requires HSET + WATCH |
| Best for | small contexts (<500B/hop) | large aggregates / multi-reader access |

For typical stream_kernel workloads (context ≤ 500 bytes/hop, 5 stages), the
**full copy per hop strategy is simpler and adds only ~2,500 bytes** to the final
message. This is within the default Redis Streams entry size and well within any
pipe or ZMQ HWM. The reference strategy becomes preferable when:
- contexts carry large blobs (images, embeddings, full event logs)
- multiple downstream systems need to read the same context independently
- context update atomicity across concurrent stages is required
