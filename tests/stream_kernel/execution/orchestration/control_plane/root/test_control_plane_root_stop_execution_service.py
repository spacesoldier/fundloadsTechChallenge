from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafStopAckEvent,
    ControlPlaneRootLeafStopRequestEvent,
)


@dataclass(slots=True)
class _IpcPort:
    sends: list[dict[str, object]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False):
        self.sends.append(
            {"target_id": target_id, "payload": payload, "no_reply": no_reply}
        )
        return None


@dataclass(slots=True)
class _RootLeafCommands:
    requests: list[dict[str, object]] = field(default_factory=list)
    waits: list[dict[str, object]] = field(default_factory=list)
    result: object | None = None

    def make_stop_request(self, **kwargs: object) -> ControlPlaneRootLeafStopRequestEvent:
        self.requests.append(dict(kwargs))
        return ControlPlaneRootLeafStopRequestEvent(
            target_group=str(kwargs["target_group"]),
            worker_id=str(kwargs["worker_id"]),
            command_id=str(kwargs["command_id"]),
            reason=kwargs.get("reason") if isinstance(kwargs.get("reason"), str) else None,
        )

    def wait_stop_ack(self, **kwargs: object):
        self.waits.append(dict(kwargs))
        return self.result


def test_root_stop_execution_service_sends_typed_stop_command_and_returns_ack() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        DefaultControlPlaneRootStopExecutionService,
    )
    from stream_kernel.platform.services.runtime.control_plane_events import (
        ControlPlaneLeafStopCommand,
    )

    ipc = _IpcPort()
    commands = _RootLeafCommands(
        result=ControlPlaneLeafStopAckEvent(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-1",
            status="accepted",
        )
    )
    service = DefaultControlPlaneRootStopExecutionService(
        root_leaf_commands=commands,
        execution_ipc=ipc,
    )

    ack = service.stop_leaf(
        target_group="execution.alpha",
        worker_id="execution.alpha#1",
        command_id="stop-1",
        timeout_seconds=0.1,
        reason="shutdown",
    )

    assert isinstance(ack, ControlPlaneLeafStopAckEvent)
    assert len(ipc.sends) == 1
    sent = ipc.sends[0]
    assert sent["target_id"] == "execution.alpha#1"
    assert sent["no_reply"] is True
    assert isinstance(sent["payload"], ControlPlaneLeafStopCommand)


def test_root_stop_execution_service_raises_on_timeout() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        ControlPlaneRootStopExecutionTimeoutError,
        DefaultControlPlaneRootStopExecutionService,
    )

    service = DefaultControlPlaneRootStopExecutionService(
        root_leaf_commands=_RootLeafCommands(result=None),
        execution_ipc=_IpcPort(),
    )

    with pytest.raises(ControlPlaneRootStopExecutionTimeoutError):
        service.stop_leaf(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-timeout",
            timeout_seconds=0.01,
        )


def test_root_stop_execution_service_raises_on_negative_ack_status() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.stop_execution_service import (
        ControlPlaneRootStopExecutionFailedError,
        DefaultControlPlaneRootStopExecutionService,
    )

    service = DefaultControlPlaneRootStopExecutionService(
        root_leaf_commands=_RootLeafCommands(
            result=ControlPlaneLeafStopAckEvent(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                command_id="stop-1",
                status="rejected",
            )
        ),
        execution_ipc=_IpcPort(),
    )

    with pytest.raises(ControlPlaneRootStopExecutionFailedError, match="rejected"):
        service.stop_leaf(
            target_group="execution.alpha",
            worker_id="execution.alpha#1",
            command_id="stop-1",
            timeout_seconds=0.1,
        )
