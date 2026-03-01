# Execution IPC TCP bytes transport (TDD plan)

## Purpose

Replace implicit `multiprocessing.Pipe().send/recv` (pickle) with an explicit
bytes-only TCP transport for supervisor <-> worker communication, while staying
on platform rails (ports/adapters/services).

This plan extends:

- [runtime_ipc_bytes_and_nonblocking_observability_tdd_plan](runtime_ipc_bytes_and_nonblocking_observability_tdd_plan.md)
- [web_phase1_secure_tcp_transport_tdd_plan](web_phase1_secure_tcp_transport_tdd_plan.md)
- [web_phase2_secure_tcp_runtime_integration_tdd_plan](web_phase2_secure_tcp_runtime_integration_tdd_plan.md)

Companion description:

- [Supervisor control-plane architecture](../web/analysis/Supervisor%20control-plane%20architecture.md)

## Current state (as-is)

- Supervisor/worker control and data share `multiprocessing.Pipe` with object
  send/recv (implicit pickle).
- `SecureTcpTransport` exists but is not wired into the multiprocess boundary path.
- `tcp_local` queue/topic adapters are placeholders (decode framed bytes only).

## Target state (to-be)

- Dedicated platform port for all IPC traffic:
  - `ExecutionIpcPort` (addressed mailbox)
- TCP transport with bytes framing only (no implicit pickle).
- Minimal ACK semantics (acceptance ACK), optional fire-and-forget.
- Optional internal prioritization/backpressure by message class (address-based).
- Supervisor logic expressed as control-plane logic (services + optional system graph)
  anchored to a single owner process; runner execution remains in child processes.

## Scenario sketch: supervisor as control-plane logic (single owner process)

Goal: keep orchestration logic on platform rails while avoiding a monolithic
`bootstrap.py` implementation.

### Roles

- **Control-plane owner process**: hosts supervisor logic + IPC port.
- **Worker processes**: host runner(s) and business/system nodes.

### Control-plane flow (single owner process)

1. **Discovery/plan stage** (framework internal):
   - load discovery modules + config;
   - build scenario + system nodes plan;
   - build child bootstrap bundle(s).
2. **Lifecycle stage**:
   - spawn worker processes via lifecycle service;
   - establish IPC endpoints via IPC transport service;
   - exchange bootstrap bundle + ready signals.
3. **Execution stage**:
   - dispatch boundary inputs over IPC port (data-plane addresses);
   - collect outputs/terminal events;
   - decide stop when policy allows (explicit signal, idle timeout, or run completion).
4. **Shutdown stage**:
   - send stop command;
   - wait output-closed or timeout → force terminate.

### Where logic lives (platform rails)

- **Lifecycle service**: spawn/stop/join workers.
- **IPC transport service**: IPC port, adapters per transport (pipe/tcp).
- **Control-plane coordinator**: state machine (ready/inflight/idle), stored in
  `ControlPlaneStateStore` (in-memory now, Redis later).
- **Optional system graph**: control-plane logic can be expressed as system nodes
  in a dedicated *control-plane runner* if we later choose to reify it as DAG,
  but it remains a separate process from business runners.

## Scope

### Control-plane message class

- Handshake and lifecycle commands: bootstrap, ready, start, stop, ack.
- Reuse `ControlPlaneChannel` + `SecureTcpTransport` envelope signing.
- Control-plane traffic remains responsive even if data traffic is saturated
  when adapter prioritization is enabled.

### Data-plane message class

- Boundary dispatch inputs/outputs and telemetry payloads.
- Bytes-only payload framing with explicit codec schema.
- ACK on accepted payload (not necessarily processed).
- Fire-and-forget option for observability traffic (`no_reply`).

### Codec contract (bytes only)

- Explicit schema version.
- Deterministic envelope fields:
  - `trace_id`, `reply_to`, `span_id`, `target`
  - `payload_kind` + `payload_bytes` (base64 for transport)
- Baseline payload kinds:
  - `bytes`, `text` (utf-8), `json` (dict/list)
  - future: typed platform payloads (`TerminalEvent`, `SinkLine`, telemetry events)
- No implicit pickle at any stage of transport.

Non-goals:

- FastAPI interface integration.
- Redis-backed queue/topic adapters.
- Multi-host transport (localhost only for now).

## Proposed platform rails

- New port under `stream_kernel.platform.services.runtime`:
  - `ExecutionIpcPort` (send/recv addressed messages)
- New adapters:
  - `tcp_local_ipc` (server/client, long-lived connections)
- New service:
  - `ExecutionIpcTransportService` (builds IPC port from config)
  - injected into `MultiprocessBootstrapSupervisor` via DI so the supervisor stays
    transport-agnostic (ports/adapter boundary).

Supervisor responsibilities:

- Bind IPC listener.
- Accept child connections on startup.
- Maintain per-worker sockets for low-latency dispatch.
- Use injected IPC service/ports (no direct socket wiring inside supervisor).

Worker responsibilities:

- Connect to supervisor IPC endpoint.
- Send ready/ack over IPC port.
- Receive frames, enqueue into runner, ack when accepted.

## ACK semantics

- `accept` (default): receiver acks after enqueue/accept.
- `none` (`no_reply`): sender does not wait for ack (fire-and-forget).
- ACK payload includes:
  - `correlation_id`
  - `status` (`accepted|rejected|timeout`)
  - optional `queue_depth`, `inflight`

## TDD sequence

### Step A — RED: codec contract

- bytes-only envelope encode/decode roundtrip (no pickle).
- reject unsupported `payload_kind`.
- reject invalid base64/invalid schema.
- enforce max payload size before decode.

### Step B — RED: control-plane message class

- handshake over TCP using control-plane codec.
- ready/start/stop/ack flow without object serialization.
- failure cases: invalid kind, expired ts, bad signature.

### Step C — RED: data-plane message class

- boundary dispatch uses data-plane frames.
- ack on accept returns to supervisor.
- `no_reply` skips ack wait.
- malformed frame returns deterministic transport error.

### Step D — RED: isolation and backpressure

- control-plane responsiveness under data saturation when prioritization enabled.
- data traffic drops/blocks according to policy.

### Step E — GREEN: implementation

- implement ports/adapters/service with TCP sockets and `SecureTcpTransport`.
- integrate into `MultiprocessBootstrapSupervisor` and worker loop.

### Step F — REFACTOR

- extract codec helpers and shared TCP primitives.
- consolidate diagnostics counters for transport.

## Done criteria

- No `Connection.send/recv` in active IPC path for process supervisor mode.
- Addressed IPC port is used end-to-end (no separate control/data ports).
- Optional class-based prioritization can be enabled without changing the port API.
- Deterministic codec errors and diagnostics are visible in observability.
- Existing memory profile remains default and green.
