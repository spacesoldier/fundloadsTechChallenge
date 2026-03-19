from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneLogDispatchNode,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.observability.events import LogDispatchEvent


@dataclass(slots=True)
class _ConsoleDispatchStub:
    published: list[LogMessage] = field(default_factory=list)

    def publish(self, message: LogMessage) -> bool:
        self.published.append(message)
        return True


def test_control_plane_log_dispatch_node_publishes_and_emits_event() -> None:
    console = _ConsoleDispatchStub()
    node = ControlPlaneLogDispatchNode(console_dispatch=console)
    payload = LogMessage(level="info", message="from-observability")

    produced = node(payload, None)

    assert console.published == [payload]
    assert len(produced) == 1
    assert isinstance(produced[0], LogDispatchEvent)
    assert produced[0].payload == payload


def test_control_plane_log_dispatch_node_ignores_unknown_payload() -> None:
    console = _ConsoleDispatchStub()
    node = ControlPlaneLogDispatchNode(console_dispatch=console)

    produced = node({"unexpected": True}, None)

    assert produced == []
    assert console.published == []
