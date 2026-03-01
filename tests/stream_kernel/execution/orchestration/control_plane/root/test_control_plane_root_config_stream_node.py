from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootConfigStreamNode,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneConfigStreamService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConfigStreamCompletedEvent,
    ControlPlaneRootPulse,
    SystemRuntimeConfigRecord,
)


@dataclass(slots=True)
class _ConfigStreamService(ControlPlaneConfigStreamService):
    produced: list[object]

    def stream(self, runtime: dict[str, object]) -> list[object]:
        _ = runtime
        return list(self.produced)


def test_root_config_stream_node_emits_records_and_completed_event() -> None:
    events = [
        SystemRuntimeConfigRecord(
            source="runtime",
            section="system_runtime",
            record_id="system_runtime:0",
            payload={"strict": True},
        ),
        ControlPlaneConfigStreamCompletedEvent(runtime={"strict": True}, record_count=1),
    ]
    node = ControlPlaneRootConfigStreamNode(config_stream=_ConfigStreamService(events))

    produced = node(ControlPlaneRootPulse(runtime={"strict": True}), None)

    assert produced == events
