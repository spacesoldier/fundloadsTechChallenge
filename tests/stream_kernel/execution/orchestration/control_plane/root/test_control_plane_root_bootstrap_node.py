from __future__ import annotations

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryStartRequestedEvent,
    ControlPlaneRootPulse,
)
from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootBootstrapNode,
)


def test_root_bootstrap_emits_discovery_start_requested() -> None:
    node = ControlPlaneRootBootstrapNode()
    runtime = {"platform": {"process_groups": []}}

    produced = node(ControlPlaneRootPulse(runtime=runtime), None)

    assert produced == [ControlPlaneDiscoveryStartRequestedEvent(runtime=runtime)]
