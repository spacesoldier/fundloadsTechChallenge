# Web Phase 5pre Step D: multiprocess lifecycle supervisor TDD spec

## Goal

Implement real process-group lifecycle supervisor behavior behind
`BootstrapSupervisor` and make Step-B `P5PRE-SUP-*` contracts green.

This step provides the first production-like multiprocess baseline without
bringing in the full worker control/data-plane orchestration yet.

## Scope

In scope:

- dedicated `MultiprocessBootstrapSupervisor` service;
- spawn-based worker process startup per process-group;
- configurable workers-per-group (`configure_process_groups`);
- deterministic readiness check (`wait_ready`);
- graceful and forced stop paths with lifecycle event stream;
- process snapshot API for diagnostics/tests.

Out of scope:

- full bootstrap handshake over control plane in worker runtime;
- heartbeat protocol and crash recovery policy;
- SIGTERM integration with global runtime signal wiring.

## Implemented contracts

## 1) Discovery/DI contract

- `MultiprocessBootstrapSupervisor` is declared as a framework service.
- Platform service exports are ordered so bootstrap discovery resolves
  multiprocess supervisor before local fallback.

## 2) Process-group startup contract

- `start_groups(group_names)` spawns daemon worker processes via
  `multiprocessing.get_context("spawn").Process(...)`.
- worker count per group comes from `configure_process_groups(...)`, default `1`.
- startup emits structured lifecycle events:
  - `worker_spawned`
  - `supervisor_start_groups`.

## 3) Readiness contract

- `wait_ready(timeout_seconds)` returns `False` for `timeout_seconds <= 0`.
- with positive timeout:
  - validates worker liveness;
  - marks workers ready after warmup window;
  - emits `worker_ready` events.

## 4) Stop contracts

- `stop_groups(graceful_timeout_seconds, drain_inflight)`:
  - primary path: uses per-worker stop event (`Event.set`) for graceful signaling;
  - fallback path: uses terminate/join when event primitives are unavailable;
  - emits `worker_stopping` with `mode=graceful`;
  - emits `worker_stopped` with `mode=graceful`;
  - raises `TimeoutError` on timeout.
- `force_terminate_groups(group_names)`:
  - kills/terminates selected groups;
  - emits `worker_stopping` and `worker_stopped` with `mode=forced`.

## 5) Diagnostics contracts

- `snapshot()` returns per-group worker entries:
  - `worker_id`, `pid`, `alive`, `ready`, `os_pid`.
- `lifecycle_events()` returns structured event list for orchestration logs/tests.

## Environment note

In constrained sandboxes `multiprocessing.SemLock` may be denied.
To keep runtime portable for this stage:

- primary path remains `Event`-based graceful signaling;
- fallback path activates automatically when `Event` allocation raises
  `PermissionError`/`OSError`/runtime primitive errors.

## TDD evidence

Primary:

- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_contract.py`

Regression:

- `.venv/bin/pytest -q tests/stream_kernel/application_context/test_service_decorator.py`
- `.venv/bin/pytest -q tests/stream_kernel/execution/orchestration/test_builder.py -k process_supervisor`
- `.venv/bin/pytest -q tests/stream_kernel/platform/services/test_bootstrap_supervisor_event_fallback.py`

## Remaining follow-ups (next steps)

- wire control-plane handshake (`control.bootstrap_bundle/ready/start_work`) into
  spawned workers;
- add heartbeat timeouts and failure state transitions;
- connect lifecycle event stream to platform logging/observability adapters.
