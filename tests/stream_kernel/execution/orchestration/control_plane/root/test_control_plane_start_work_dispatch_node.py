from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.orchestration.control_plane import (
    ControlPlaneRootLeafStartWorkDispatchNode,
    ControlPlaneStartWorkReadinessNode,
    ControlPlaneStartWorkDispatchNode,
)
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    compose_execution_ipc_worker_target_id,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
    ControlPlaneLaunchPlanEvent,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
    ControlPlaneRootLeafStartWorkCommand,
    ControlPlaneLeafStartWorkEvent,
    ControlPlaneStartWorkEvent,
    SystemRuntimeConfigRecord,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    InMemoryControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    InMemoryControlPlaneStateService,
)


@dataclass(slots=True)
class _ControlLaneIpc:
    sent: list[tuple[str, object, bool]] = field(default_factory=list)

    def send(self, target_id: str, payload: object, *, no_reply: bool = False) -> None:
        self.sent.append((target_id, payload, bool(no_reply)))


def test_control_plane_start_work_dispatch_fans_out_start_to_all_workers() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="execution.features",
                        workers=1,
                        nodes=("compute_time_keys", "compute_features"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(state=state)

    produced = node(
        ControlPlaneStartWorkEvent(
            source_targets=("source:source",),
        ),
        None,
    )
    assert len(produced) == 2
    assert isinstance(produced[0], LogMessage)
    assert produced[0].fields.get("event") == "control_plane.runtime.start_work_commands_enqueued"
    command = produced[1]
    assert isinstance(command, ControlPlaneRootLeafStartWorkCommand)
    assert command.worker_id == "execution.ingress#1"
    assert command.target_group == "execution.ingress"
    assert command.source_targets == ("source:source",)
    assert isinstance(command.command_id, str) and command.command_id


def test_control_plane_start_work_dispatch_is_idempotent() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(state=state)

    first = node(ControlPlaneStartWorkEvent(), None)
    second = node(ControlPlaneStartWorkEvent(), None)

    assert len(first) == 2
    assert isinstance(first[0], LogMessage)
    assert second == []
    assert isinstance(first[1], ControlPlaneRootLeafStartWorkCommand)


def test_control_plane_start_work_dispatch_targets_each_worker_when_group_has_multiple_workers() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=2,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                )
            )
        )
    )
    node = ControlPlaneStartWorkDispatchNode(state=state)

    produced = node(ControlPlaneStartWorkEvent(source_targets=("source:source",)), None)

    assert len(produced) == 3
    assert isinstance(produced[0], LogMessage)
    assert produced[0].fields.get("event") == "control_plane.runtime.start_work_commands_enqueued"
    commands = [item for item in produced[1:] if isinstance(item, ControlPlaneRootLeafStartWorkCommand)]
    assert len(commands) == 2
    assert {item.worker_id for item in commands} == {
        "execution.ingress#1",
        "execution.ingress#2",
    }


def test_root_leaf_start_work_dispatch_sends_command_to_control_lane() -> None:
    ipc = _ControlLaneIpc()
    node = ControlPlaneRootLeafStartWorkDispatchNode(control_lane_ipc=ipc)  # type: ignore[arg-type]
    command = ControlPlaneRootLeafStartWorkCommand(
        target_group="execution.ingress",
        worker_id="execution.ingress#1",
        source_targets=("source:source",),
        command_id="start-1",
    )

    produced = node(command, None)

    assert produced == []
    assert len(ipc.sent) == 1
    target_id, payload, no_reply = ipc.sent[0]
    assert target_id == compose_execution_ipc_worker_target_id(
        "execution.ingress#1",
        lane=EXECUTION_IPC_LANE_CONTROL,
    )
    assert no_reply is True
    assert isinstance(payload, ControlPlaneLeafStartWorkEvent)
    assert payload.source_targets == ("source:source",)
    assert payload.command_id == "start-1"


def test_start_work_readiness_emits_start_when_all_non_observability_workers_applied() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    store.append(
        SystemRuntimeConfigRecord(
            source="runtime",
            section="system_runtime",
            record_id="system_runtime:0",
            payload={
                "platform": {
                    "readiness": {
                        "enabled": True,
                        "start_work_on_all_groups_ready": True,
                    }
                }
            },
        )
    )
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                    ControlPlaneGroupSpec(
                        group_name="system.observability",
                        workers=1,
                        nodes=("system.obs.trace_dispatch",),
                    ),
                )
            )
        )
    )
    state.append_event(
        ControlPlaneLeafConfigCardEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg-1",
            run_id="run",
            scenario_id="scenario",
            group_name="execution.ingress",
            nodes=("source:source",),
            runner_profile="async",
            worker_slot=0,
            config_revision=1,
        )
    )
    node = ControlPlaneStartWorkReadinessNode(state=state, config_store=store)

    produced = node(
        ControlPlaneLeafConfigAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg-1",
            status="applied",
        ),
        None,
    )

    assert produced == [ControlPlaneStartWorkEvent(source_targets=("source:source",))]


def test_start_work_readiness_respects_start_work_on_all_groups_ready_flag() -> None:
    state = InMemoryControlPlaneStateService(store=InMemoryKvStore())
    store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    store.append(
        SystemRuntimeConfigRecord(
            source="runtime",
            section="system_runtime",
            record_id="system_runtime:0",
            payload={
                "platform": {
                    "readiness": {
                        "enabled": True,
                        "start_work_on_all_groups_ready": False,
                    }
                }
            },
        )
    )
    state.append_event(
        ControlPlaneLaunchPlanEvent(
            plan=ControlPlaneLaunchPlan(
                groups=(
                    ControlPlaneGroupSpec(
                        group_name="execution.ingress",
                        workers=1,
                        nodes=("source:source", "ingress_line_bridge"),
                    ),
                )
            )
        )
    )
    state.append_event(
        ControlPlaneLeafConfigCardEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg-1",
            run_id="run",
            scenario_id="scenario",
            group_name="execution.ingress",
            nodes=("source:source",),
            runner_profile="async",
            worker_slot=0,
            config_revision=1,
        )
    )
    node = ControlPlaneStartWorkReadinessNode(state=state, config_store=store)

    produced = node(
        ControlPlaneLeafConfigAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg-1",
            status="applied",
        ),
        None,
    )

    assert produced == []
