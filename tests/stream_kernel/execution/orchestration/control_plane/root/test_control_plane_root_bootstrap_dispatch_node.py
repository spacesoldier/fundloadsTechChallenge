from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootBootstrapDispatchNode,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
    ControlPlaneRootPulse,
)


@dataclass(slots=True)
class _RootRuntimeBootstrapStub:
    calls: list[dict[str, object]] = field(default_factory=list)

    def prepare_root_runtime(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def test_root_bootstrap_dispatch_calls_runtime_prepare_from_init_discovery() -> None:
    bootstrap = _RootRuntimeBootstrapStub()
    node = ControlPlaneRootBootstrapDispatchNode(runtime_bootstrap=bootstrap)
    payload = ControlPlaneInitEvent(
        runtime={"platform": {"bootstrap": {"mode": "process_supervisor"}}},
        discovery={
            "root_runtime_prepare": {
                "run_id": "run-1",
                "scenario_id": "scenario-1",
                "config": {"runtime": {}},
                "adapters": {"source": {"kind": "x"}},
                "discovery_modules": ("pkg.a", "pkg.b"),
            }
        },
    )

    produced = node(payload, None)

    assert produced == [ControlPlaneRootPulse(runtime=payload.runtime)]
    assert len(bootstrap.calls) == 1
    assert bootstrap.calls[0]["run_id"] == "run-1"
    assert bootstrap.calls[0]["scenario_id"] == "scenario-1"
    assert bootstrap.calls[0]["discovery_modules"] == ["pkg.a", "pkg.b"]


def test_root_bootstrap_dispatch_ignores_worker_process_role() -> None:
    bootstrap = _RootRuntimeBootstrapStub()
    node = ControlPlaneRootBootstrapDispatchNode(runtime_bootstrap=bootstrap)
    payload = ControlPlaneInitEvent(
        runtime={"__process_role": "worker"},
        discovery={
            "root_runtime_prepare": {
                "run_id": "run-1",
                "scenario_id": "scenario-1",
            }
        },
    )

    produced = node(payload, None)

    assert produced == []
    assert bootstrap.calls == []
