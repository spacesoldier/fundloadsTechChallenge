from __future__ import annotations

from types import SimpleNamespace


def test_default_leaf_runtime_boundary_batch_service_delegates_to_boundary_runtime(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service as mod

    seen: list[tuple[object, list[object], bool]] = []

    def _execute(*, child: object, inputs: list[object], finalize: bool = True):
        seen.append((child, list(inputs), finalize))
        return ["ok"]

    monkeypatch.setattr(mod, "execute_child_boundary_loop_with_runtime", _execute)

    child = SimpleNamespace(name="child-runtime")
    service = mod.DefaultLeafRuntimeBoundaryBatchService()
    outputs = service.execute_boundary_batch(child=child, inputs=[{"x": 1}])

    assert outputs == ["ok"]
    assert seen == [(child, [{"x": 1}], False)]
