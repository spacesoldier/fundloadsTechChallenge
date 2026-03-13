from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafHelloEvent,
)


def test_bootstrap_leaf_worker_runtime_from_bundle_delegates_to_runtime_bootstrap_service(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime as mod

    fake_child = SimpleNamespace(runner_profile_effective="async")
    seen: list[object] = []

    class _Service:
        def bootstrap_runtime(self, *, bundle: object) -> object:
            seen.append(bundle)
            return fake_child

    monkeypatch.setattr(mod, "resolve_leaf_runtime_bootstrap_service", lambda: _Service())

    bundle = {"bundle": "raw"}
    session = mod.bootstrap_leaf_worker_runtime_from_bundle(
        bundle=bundle,
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
    )

    assert seen == [bundle]
    assert session.child is fake_child
    assert session.worker_id == "execution.alpha#1"
    assert session.group_name == "execution.alpha"
    assert session.runner_profile_requested == "auto"
    assert session.runner_profile_effective == "async"


def test_build_leaf_hello_event_uses_session_fields(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime as mod

    monkeypatch.setattr(mod.os, "getpid", lambda: 43210)
    session = mod.LeafWorkerRuntimeSession(
        child=SimpleNamespace(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )

    event = mod.build_leaf_hello_event(session)

    assert isinstance(event, ControlPlaneLeafHelloEvent)
    assert event.target_group == "execution.alpha"
    assert event.worker_id == "execution.alpha#1"
    assert event.pid == 43210
    assert event.runner_profile == "sync"


def test_execute_leaf_boundary_batch_delegates_with_existing_child_runtime(
    monkeypatch,
) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime as mod

    session = mod.LeafWorkerRuntimeSession(
        child=SimpleNamespace(name="child-runtime"),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )
    seen: list[tuple[object, list[object], object | None, int]] = []

    class _BoundaryService:
        def execute_boundary_batch(
            self,
            *,
            child: object,
            inputs: list[object],
            stream_callback: object | None = None,
            stream_batch_max_items: int = 1,
            finalize_runtime: bool = False,
        ) -> list[object]:
            _ = finalize_runtime
            seen.append((child, list(inputs), stream_callback, stream_batch_max_items))
            return ["ok"]

    monkeypatch.setattr(mod, "resolve_leaf_runtime_boundary_service", lambda: _BoundaryService())

    outputs = mod.execute_leaf_boundary_batch(session=session, inputs=[{"x": 1}])

    assert outputs == ["ok"]
    assert seen == [(session.child, [{"x": 1}], None, 1)]
