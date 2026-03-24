from __future__ import annotations

from types import SimpleNamespace
import pytest


def test_default_leaf_runtime_boundary_batch_service_delegates_to_boundary_runtime(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service as mod

    seen: list[tuple[object, list[object], bool]] = []

    def _execute(
        *,
        child: object,
        inputs: list[object],
        finalize: bool = True,
        stream_callback=None,
        stream_batch_max_items: int = 1,
    ):
        _ = (stream_callback, stream_batch_max_items)
        seen.append((child, list(inputs), finalize))
        return ["ok"]

    monkeypatch.setattr(mod, "execute_child_boundary_loop_with_runtime", _execute)

    child = SimpleNamespace(name="child-runtime")
    service = mod.DefaultLeafRuntimeBoundaryBatchService()
    outputs = service.execute_boundary_batch(child=child, inputs=[{"x": 1}])

    assert outputs == ["ok"]
    assert seen == [(child, [{"x": 1}], False)]


def test_boundary_runtime_prefers_child_runtime_worker_id_over_env(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime as mod
    from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
        ChildBoundaryInput,
    )

    captured_kwargs: dict[str, object] = {}

    class _Runner:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        def run(self) -> None:
            return None

        def on_run_end(self) -> None:
            return None

    monkeypatch.setenv("STREAM_KERNEL_WORKER_ID", "system.observability#1")
    monkeypatch.setattr(mod, "SyncRunner", _Runner)
    monkeypatch.setattr(mod, "AsyncRunner", _Runner)
    monkeypatch.setattr(mod, "select_group_planning_steps", lambda **_kwargs: {})
    monkeypatch.setattr(mod, "_runtime_process_group_is_declared", lambda **_kwargs: False)
    monkeypatch.setattr(mod, "_resolve_context_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_observability_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_routing_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_apply_startup_bindings_for_boundary_runtime", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_leaf_debug_logging_service", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_runtime_debug_buffer", lambda _scope: None)
    monkeypatch.setattr(mod, "_restore_boundary_control_plane_rail", lambda **_kwargs: (lambda *_a, **_k: []))

    child = SimpleNamespace(
        scenario_steps={"business.step": (lambda payload, _ctx: [payload])},
        runtime={"__worker_id": "execution.egress#1"},
        process_group="execution.egress",
        full_context_nodes=set(),
        scenario_scope=object(),
        runner_profile_effective="sync",
        runner_profile_nodes={},
    )
    outputs = mod.execute_child_boundary_loop(
        child=child,
        inputs=[
            ChildBoundaryInput(
                payload={"x": 1},
                dispatch_group="execution.egress",
                target="business.step",
                trace_id="trace-1",
            )
        ],
    )

    assert outputs == []
    assert captured_kwargs.get("worker_id") == "execution.egress#1"


def test_boundary_runtime_raises_when_required_control_plane_rails_cannot_be_restored(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime as mod
    from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
        ChildRuntimeBootstrapError,
        ChildBoundaryInput,
    )

    debug_events: list[dict[str, object]] = []

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self) -> None:
            return None

        def on_run_end(self) -> None:
            return None

    def _capture_debug(*, event: str, service: object | None = None, **fields: object) -> None:
        _ = service
        debug_events.append({"event": event, **fields})

    monkeypatch.setattr(mod, "leaf_debug_log", _capture_debug)
    monkeypatch.setattr(mod, "SyncRunner", _Runner)
    monkeypatch.setattr(mod, "AsyncRunner", _Runner)
    monkeypatch.setattr(mod, "select_group_planning_steps", lambda **_kwargs: {})
    monkeypatch.setattr(mod, "_runtime_process_group_is_declared", lambda **_kwargs: False)
    monkeypatch.setattr(mod, "_resolve_context_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_observability_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_routing_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_apply_startup_bindings_for_boundary_runtime", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_leaf_debug_logging_service", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_runtime_debug_buffer", lambda _scope: None)
    monkeypatch.setattr(mod, "_restore_boundary_control_plane_rail", lambda **_kwargs: None)

    child = SimpleNamespace(
        scenario_steps={"business.step": (lambda payload, _ctx: [payload])},
        runtime={"__worker_id": "execution.egress#1"},
        process_group="execution.egress",
        full_context_nodes=set(),
        scenario_scope=object(),
        runner_profile_effective="sync",
        runner_profile_nodes={},
    )
    with pytest.raises(
        ChildRuntimeBootstrapError,
        match="missing required control-plane rail",
    ):
        _ = mod.execute_child_boundary_loop(
            child=child,
            inputs=[
                ChildBoundaryInput(
                    payload={"x": 1},
                    dispatch_group="execution.egress",
                    target="business.step",
                    trace_id="trace-1",
                )
            ],
        )

    missing_events = [
        item for item in debug_events if item.get("event") == "leaf.boundary_runtime.missing_cp_rail"
    ]
    assert len(missing_events) == 1
    missing_names = {item.get("node_name") for item in missing_events}
    assert missing_names == {"system.cp.leaf_tombstone_finalize"}


def test_boundary_runtime_restores_required_control_plane_rails(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_runtime as mod
    from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import (
        ChildBoundaryInput,
    )

    captured_kwargs: dict[str, object] = {}

    class _Runner:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        def run(self) -> None:
            return None

        def on_run_end(self) -> None:
            return None

    monkeypatch.setattr(mod, "SyncRunner", _Runner)
    monkeypatch.setattr(mod, "AsyncRunner", _Runner)
    monkeypatch.setattr(mod, "select_group_planning_steps", lambda **_kwargs: {})
    monkeypatch.setattr(mod, "_runtime_process_group_is_declared", lambda **_kwargs: False)
    monkeypatch.setattr(mod, "_resolve_context_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_observability_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_resolve_routing_service", lambda _scope: object())
    monkeypatch.setattr(mod, "_apply_startup_bindings_for_boundary_runtime", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_leaf_debug_logging_service", lambda _scope: None)
    monkeypatch.setattr(mod, "_resolve_runtime_debug_buffer", lambda _scope: None)
    monkeypatch.setattr(mod, "_restore_boundary_control_plane_rail", lambda **_kwargs: (lambda *_a, **_k: []))

    child = SimpleNamespace(
        scenario_steps={"business.step": (lambda payload, _ctx: [payload])},
        runtime={"__worker_id": "execution.egress#1"},
        process_group="execution.egress",
        full_context_nodes=set(),
        scenario_scope=object(),
        runner_profile_effective="sync",
        runner_profile_nodes={},
    )
    outputs = mod.execute_child_boundary_loop(
        child=child,
        inputs=[
            ChildBoundaryInput(
                payload={"x": 1},
                dispatch_group="execution.egress",
                target="business.step",
                trace_id="trace-1",
            )
        ],
    )

    assert outputs == []
    nodes = captured_kwargs.get("nodes")
    assert isinstance(nodes, dict)
    assert "system.cp.leaf_tombstone_finalize" in nodes
    assert "system.cp.leaf_reply_dispatch" in nodes
