# Web Phase 5pre Step C: control-plane implementation TDD spec

## Goal

Implement framework-native control-plane primitives required by multiprocess
supervisor rollout:

- typed control messages;
- secure wire codec over existing `SecureTcpTransport`;
- deterministic control session state machine.

This step must be green in isolation before worker process lifecycle work
(Step D).

## Scope

In scope:

- control message model (`bootstrap_bundle`, `ready`, `heartbeat`, `start_work`,
  `stop`, `ack`);
- control message framing/signing/verification via `SecureTcpTransport`;
- bootstrap bundle serialization helpers;
- control session rules:
  - bootstrap only once per worker,
  - `ready` only after bootstrap,
  - `start_work` blocked until required workers are ready.

Out of scope:

- real process spawn and worker supervision internals (Step D);
- full runtime orchestration wiring in supervisor lifecycle path (Step D/E);
- external observability exporters (Step F).

## Contracts

## 1) Control message model

`ControlMessage` fields:

- `kind: str` from control kinds set;
- `correlation_id: str` non-empty;
- `worker_id: str | None`;
- `payload: dict[str, object]`.

Control kinds:

- `control.bootstrap_bundle`
- `control.ready`
- `control.heartbeat`
- `control.start_work`
- `control.stop`
- `control.ack`

## 2) Bootstrap bundle wire contract

`BootstrapKeyBundle` must be serializable to mapping and back:

- `created_at_epoch: int`
- `execution_ipc.secret_mode: str`
- `execution_ipc.kdf: str`
- `execution_ipc.master_secret_b64: str`
- `execution_ipc.signing_secret_b64: str`

## 3) Secure control codec contract

`ControlPlaneChannel` wraps `SecureTcpTransport`:

- encodes `ControlMessage` to signed framed bytes;
- decodes framed bytes back to `ControlMessage`;
- rejects malformed/tampered/replayed/expired traffic via existing secure
  transport error categories.

## 4) Control session contract

`ControlPlaneSession.process(message)` rules:

- `bootstrap_bundle`:
  - requires `worker_id`,
  - accepts first bootstrap for worker and emits `ack`,
  - second bootstrap for same worker is deterministic protocol error.
- `ready`:
  - requires prior bootstrap for the same worker,
  - emits `ack`.
- `start_work`:
  - payload may declare `required_workers: list[str]`,
  - if any required worker is not ready, deterministic protocol error.

## TDD matrix

- `P5PRE-CTRL-01` bootstrap bundle message roundtrip and ACK.
- `P5PRE-CTRL-02` duplicate bootstrap command rejected.
- `P5PRE-CTRL-03` invalid signature/expired ttl/replay rejected through control
  channel decode path.
- `P5PRE-CTRL-04` start-work command blocked before READY and accepted after
  READY for required workers.
- `P5PRE-CTRL-05` bootstrap bundle serialization roundtrip preserves key
  material bytes.

## Test placement

- `tests/stream_kernel/execution/transport/test_control_plane.py`

## Exit criteria (Step C)

- all `P5PRE-CTRL-*` tests are green;
- no new ad-hoc transport path is introduced outside `SecureTcpTransport`;
- control-plane module is isolated and reusable by Step D supervisor
  implementation.

## Step C completion evidence

Implementation:

- `src/stream_kernel/execution/transport/control_plane.py`

Tests:

- `tests/stream_kernel/execution/transport/test_control_plane.py`

Executed:

- `.venv/bin/pytest -q tests/stream_kernel/execution/transport/test_control_plane.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/transport/test_secure_tcp_transport.py tests/stream_kernel/execution/transport/test_bootstrap_keys.py`
