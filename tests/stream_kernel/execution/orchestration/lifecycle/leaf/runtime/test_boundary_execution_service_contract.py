from __future__ import annotations

from types import SimpleNamespace

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
)


def test_default_leaf_boundary_execution_service_delegates_to_leaf_runtime_helper(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service as mod

    seen: list[tuple[object, list[object], bool, object | None, int]] = []

    def _execute(
        *,
        session: object,
        inputs: list[object],
        finalize_runtime: bool = False,
        stream_callback: object | None = None,
        stream_batch_max_items: int = 1,
    ):
        seen.append((session, list(inputs), finalize_runtime, stream_callback, stream_batch_max_items))
        return [{"ok": True}]

    monkeypatch.setattr(mod, "execute_leaf_boundary_batch", _execute)

    service = mod.DefaultLeafBoundaryExecutionService()
    session = SimpleNamespace(child=object(), worker_id="w#1", group_name="g")

    outputs = service.execute(session=session, inputs=[{"x": 1}])

    assert outputs == [{"ok": True}]
    assert seen == [(session, [{"x": 1}], False, None, 1)]
