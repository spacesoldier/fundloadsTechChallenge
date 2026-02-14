from __future__ import annotations

import pytest

from stream_kernel.platform.services.bootstrap import MultiprocessBootstrapSupervisor


def test_multiprocess_supervisor_uses_event_stop_when_available() -> None:
    # Primary path: graceful signaling should use multiprocessing.Event when environment supports sem primitives.
    supervisor = MultiprocessBootstrapSupervisor()
    try:
        try:
            _ = supervisor._ctx.Event()  # noqa: SLF001 - test probe of runtime capability
        except (PermissionError, OSError):
            pytest.skip("SemLock primitives are not available in this environment")

        supervisor.configure_process_groups([{"name": "execution.cpu", "workers": 1}])
        supervisor.start_groups(["execution.cpu"])
        assert supervisor.wait_ready(1) is True
        snapshot = supervisor.snapshot()
        workers = snapshot.get("execution.cpu", [])
        assert isinstance(workers, list)
        assert len(workers) == 1
        assert workers[0].get("has_stop_event") is True
        supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
    finally:
        # Keep tests resilient if graceful stop fails in partially initialized states.
        supervisor.force_terminate_groups(["execution.cpu"])


def test_multiprocess_supervisor_falls_back_to_terminate_when_event_unavailable() -> None:
    # Fallback path: if Event factory fails, supervisor still starts/stops workers via terminate path.
    supervisor = MultiprocessBootstrapSupervisor()
    try:
        supervisor._event_factory = lambda: (_ for _ in ()).throw(PermissionError("semaphore denied"))  # noqa: SLF001

        supervisor.configure_process_groups([{"name": "execution.cpu", "workers": 1}])
        supervisor.start_groups(["execution.cpu"])
        assert supervisor.wait_ready(1) is True
        snapshot = supervisor.snapshot()
        workers = snapshot.get("execution.cpu", [])
        assert isinstance(workers, list)
        assert len(workers) == 1
        assert workers[0].get("has_stop_event") is False

        supervisor.stop_groups(graceful_timeout_seconds=1, drain_inflight=True)
        events = supervisor.lifecycle_events()
        assert any(event.get("kind") == "stop_event_unavailable" for event in events)
        assert any(
            event.get("kind") == "worker_spawned" and event.get("stop_strategy") == "terminate_fallback"
            for event in events
        )
    finally:
        supervisor.force_terminate_groups(["execution.cpu"])
