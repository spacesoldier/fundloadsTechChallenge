from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafBoundaryExecuteCommand,
    ControlPlaneLeafBoundaryResultEvent,
)


def test_default_leaf_boundary_execution_service_delegates_to_leaf_runtime_helper(monkeypatch) -> None:
    import stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service as mod

    seen: list[tuple[object, list[object], bool]] = []

    def _execute(*, session: object, inputs: list[object], finalize_runtime: bool = False):
        seen.append((session, list(inputs), finalize_runtime))
        return [{"ok": True}]

    monkeypatch.setattr(mod, "execute_leaf_boundary_batch", _execute)

    service = mod.DefaultLeafBoundaryExecutionService()
    session = SimpleNamespace(child=object(), worker_id="w#1", group_name="g")

    outputs = service.execute(session=session, inputs=[{"x": 1}])

    assert outputs == [{"ok": True}]
    assert seen == [(session, [{"x": 1}], False)]


def test_leaf_worker_command_loop_service_delegates_boundary_to_boundary_execution_service() -> None:
    from stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service import (
        DefaultLeafWorkerCommandLoopService,
    )

    class _Activation:
        def apply_config(self, **_kwargs):
            raise AssertionError("activation service should not be called for boundary command")

    class _BoundaryExec:
        def __init__(self) -> None:
            self.calls: list[tuple[object, list[object], bool]] = []

        def execute(
            self,
            *,
            session: object,
            inputs: list[object],
            finalize_runtime: bool = False,
        ):
            self.calls.append((session, list(inputs), finalize_runtime))
            return [{"handled": len(inputs)}]

    @dataclass(slots=True)
    class _ExecutionIpc:
        incoming: list[object] = field(default_factory=list)
        sent: list[tuple[str, object, bool]] = field(default_factory=list)

        def recv(self, target_id: str, *, timeout: float | None = None):
            _ = timeout
            if not self.incoming:
                return None
            return ExecutionIpcMessage(target_id=target_id, payload=self.incoming.pop(0), ts_epoch_ms=0)

        def send(self, target_id: str, payload: object, *, no_reply: bool = False):
            self.sent.append((target_id, payload, bool(no_reply)))
            return None

    @dataclass(slots=True)
    class _StopEvent:
        is_set_now: bool = False

        def is_set(self) -> bool:
            return self.is_set_now

    session = SimpleNamespace(
        child=object(),
        worker_id="execution.alpha#1",
        group_name="execution.alpha",
        runner_profile_requested="auto",
        runner_profile_effective="sync",
    )
    ipc = _ExecutionIpc(
        incoming=[
            ControlPlaneLeafBoundaryExecuteCommand(
                target_group="execution.alpha",
                worker_id="execution.alpha#1",
                request_id="req-1",
                inputs=({"payload": 1},),
                finalize=True,
            )
        ]
    )
    boundary_exec = _BoundaryExec()
    service = DefaultLeafWorkerCommandLoopService(
        activation_service=_Activation(),
        boundary_execution_service=boundary_exec,  # RED: command loop should delegate boundary execution
        execution_ipc=ipc,
    )

    result = service.run_control_iteration(
        session=session,
        control_pipe=object(),
        stop_event=_StopEvent(False),
        poll_seconds=0.01,
    )

    assert result == "boundary_executed"
    assert boundary_exec.calls == [(session, [{"payload": 1}], False)]
    assert len(ipc.sent) == 1
    event = ipc.sent[0][1]
    assert isinstance(event, ControlPlaneLeafBoundaryResultEvent)
    assert event.status == "completed"
    assert event.outputs == ({"handled": 1},)
