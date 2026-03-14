from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.lifecycle.leaf.command.channel_services import (
    DefaultLeafCommandChannelIngressService,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    ExecutionIpcMessage,
    compose_execution_ipc_worker_target_id,
)


@dataclass(slots=True)
class _BufferedIpcPort:
    calls: list[tuple[str, str, float | None]] = field(default_factory=list)

    def recv(self, target_id: str, *, timeout: float | None = None):
        self.calls.append(("recv", target_id, timeout))
        raise AssertionError("recv() should not be used by leaf ingress polling path")

    def recv_buffered(self, target_id: str, *, timeout: float | None = None):
        self.calls.append(("recv_buffered", target_id, timeout))
        return ExecutionIpcMessage(target_id=target_id, payload={"ok": True}, ts_epoch_ms=1)


def test_leaf_channel_ingress_prefers_buffered_recv_when_available() -> None:
    ipc = _BufferedIpcPort()
    service = DefaultLeafCommandChannelIngressService(
        control_lane_ipc=ipc,  # type: ignore[arg-type]
        data_lane_ipc=ipc,  # type: ignore[arg-type]
        trace_lane_ipc=ipc,  # type: ignore[arg-type]
        log_lane_ipc=ipc,  # type: ignore[arg-type]
        metric_lane_ipc=ipc,  # type: ignore[arg-type]
    )

    payload = service.poll_next_message_for_lane(
        worker_id="execution.alpha#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
        timeout_seconds=0.02,
    )

    lane_target = compose_execution_ipc_worker_target_id(
        "execution.alpha#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    assert payload == {"ok": True}
    assert ipc.calls == [("recv_buffered", lane_target, 0.02)]
