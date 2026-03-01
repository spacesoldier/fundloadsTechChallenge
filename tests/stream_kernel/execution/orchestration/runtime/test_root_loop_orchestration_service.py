from __future__ import annotations

from dataclasses import dataclass, field
import pytest

from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
    ControlPlaneRootReplyIngressService,
)
from stream_kernel.platform.services.runtime.control_plane_events import ControlPlaneRootPulse
from stream_kernel.platform.services.runtime.control_plane_state import ControlPlaneStateService
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.routing.envelope import Envelope

from stream_kernel.execution.orchestration.runtime.root_loop_orchestration_service import (
    RootRunnerLoopOrchestrationService,
)


@dataclass(slots=True)
class _SyncRunner:
    allow_external_deliveries: bool = False
    external_deliveries: list[Envelope] = field(default_factory=list)
    run_calls: int = 0
    loop_calls: int = 0

    def run(self) -> None:
        self.run_calls += 1

    def run_until_stopped(self, *, poll_timeout_seconds: float, idle_timeout_seconds: float | None) -> None:  # noqa: ARG002
        self.loop_calls += 1


@dataclass(slots=True)
class _Barrier(ControlPlaneStartupBarrierService):
    open_after_loop_calls: int | None = None
    runner: _SyncRunner | None = None

    def mark_discovery_completed(self, *, runtime: dict[str, object]) -> bool:  # noqa: ARG002
        return False

    def mark_config_completed(self, *, runtime: dict[str, object]) -> bool:  # noqa: ARG002
        return False

    def is_open(self) -> bool:
        if self.open_after_loop_calls is None:
            return False
        if self.runner is None:
            return False
        return self.runner.loop_calls >= self.open_after_loop_calls

    def reset(self) -> None:
        return None


@dataclass(slots=True)
class _Scope:
    barrier: ControlPlaneStartupBarrierService

    def resolve(self, port_type: str, data_type: object) -> object:  # noqa: ANN401
        if port_type == "service":
            return self.barrier
        raise ValueError(f"unsupported resolve({port_type!r}, {data_type!r})")


@dataclass(slots=True)
class _ReplyIngress(ControlPlaneRootReplyIngressService):
    calls: list[tuple[str, float, int]] = field(default_factory=list)

    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        self.calls.append((worker_id, timeout_seconds, max_items))
        return 0


@dataclass(slots=True)
class _State(ControlPlaneStateService):
    _events: list[object]

    def append_event(self, event: object) -> None:
        self._events.append(event)

    def events(self) -> list[object]:
        return list(self._events)


@dataclass(slots=True)
class _ReplyIngressWithAckProgress(ControlPlaneRootReplyIngressService):
    state: _State
    expected_worker_ids: tuple[str, ...]
    calls: int = 0

    def drain_worker_replies(
        self,
        *,
        worker_id: str,
        timeout_seconds: float = 0.0,
        max_items: int = 64,
    ) -> int:
        _ = (timeout_seconds, max_items)
        self.calls += 1
        if self.calls == 1:
            for wid in self.expected_worker_ids:
                self.state.append_event(
                    {
                        "kind": "leaf.config_ack",
                        "worker_id": wid,
                        "status": "applied",
                    }
                )
        return 1


@dataclass(slots=True)
class _ScopeWithStartupServices:
    barrier: ControlPlaneStartupBarrierService
    reply_ingress: ControlPlaneRootReplyIngressService
    state: ControlPlaneStateService

    def resolve(self, port_type: str, data_type: object) -> object:  # noqa: ANN401
        if port_type != "service":
            raise ValueError(f"unsupported resolve({port_type!r}, {data_type!r})")
        if data_type is ControlPlaneStartupBarrierService:
            return self.barrier
        if data_type is ControlPlaneRootReplyIngressService:
            return self.reply_ingress
        if data_type is ControlPlaneStateService:
            return self.state
        raise ValueError(f"unsupported service contract: {data_type!r}")


def test_root_loop_service_detects_root_pulse_and_splits_inputs() -> None:
    service = RootRunnerLoopOrchestrationService()
    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}}),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]

    assert service.has_root_control_plane_pulse(inputs) is True
    startup, deferred = service.split_root_control_plane_inputs(inputs)
    assert len(startup) == 1
    assert len(deferred) == 1
    assert isinstance(startup[0][1], ControlPlaneRootPulse)
    assert isinstance(deferred[0][1], Envelope)


def test_root_loop_service_resolves_timeouts_from_root_pulse_runtime() -> None:
    service = RootRunnerLoopOrchestrationService()
    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [{"name": "execution.alpha"}],
                    "runner_loop": {"poll_timeout_ms": 7.5, "idle_timeout_ms": 250},
                }
            }
        )
    ]

    poll_timeout_seconds, idle_timeout_seconds = service.resolve_runner_loop_timeouts(inputs)
    assert poll_timeout_seconds == 0.0075
    assert idle_timeout_seconds == 0.25


def test_root_loop_service_executes_sync_root_mode_with_replay() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    enqueued: list[object] = []
    replay_calls: list[dict[str, object]] = []
    drained: list[dict[str, object]] = []

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        enqueued.append((payload, run_id, scenario_id, index))

    def _replay(
        *,
        runner: object,
        scenario_scope: object,
        run_id: str,
        scenario_id: str,
        start_index: int,
        poll_timeout_seconds: float,
        idle_timeout_seconds: float | None,
    ) -> int:
        replay_calls.append(
            {
                "runner": runner,
                "scenario_scope": scenario_scope,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "start_index": start_index,
                "poll_timeout_seconds": poll_timeout_seconds,
                "idle_timeout_seconds": idle_timeout_seconds,
            }
        )
        return start_index + 1

    def _drain(*, scenario_scope: object, external_deliveries: list[Envelope]) -> list[object]:
        drained.append(
            {
                "scenario_scope": scenario_scope,
                "external_deliveries": list(external_deliveries),
            }
        )
        return []

    scope = object()
    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}}),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        enqueue_runner_input=_enqueue,
        replay_root_boundary_outputs=_replay,
        drain_root_boundary_outputs=_drain,
    )

    assert runner.allow_external_deliveries is True
    assert runner.loop_calls == 2
    assert runner.run_calls == 0
    assert len(enqueued) == 2
    assert len(replay_calls) == 2
    assert drained == []


def test_root_loop_service_waits_for_startup_barrier_before_deferred_inputs() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    barrier = _Barrier(open_after_loop_calls=2, runner=runner)
    scope = _Scope(barrier=barrier)
    enqueued: list[object] = []

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        enqueued.append((payload, run_id, scenario_id, index))

    def _replay(**_: object) -> int:
        return 10

    def _drain(**_: object) -> list[object]:
        return []

    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}}),
        Envelope(payload="business", target="biz.node", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
        replay_root_boundary_outputs=_replay,
        drain_root_boundary_outputs=_drain,
    )

    assert len(enqueued) == 2
    assert isinstance(enqueued[0][0], ControlPlaneRootPulse)
    assert isinstance(enqueued[1][0], Envelope)
    # 1) startup loop, 2) barrier wait loop, 3) deferred inputs loop
    assert runner.loop_calls == 3


def test_root_loop_service_raises_when_startup_barrier_does_not_open_within_timeout() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    barrier = _Barrier(open_after_loop_calls=None, runner=runner)
    scope = _Scope(barrier=barrier)

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    def _replay(**_: object) -> int:
        return 1

    def _drain(**_: object) -> list[object]:
        return []

    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [{"name": "execution.alpha"}],
                    "runner_loop": {
                        "poll_timeout_ms": 1,
                        "idle_timeout_ms": 1,
                        "startup_barrier_timeout_ms": 3,
                    },
                }
            }
        ),
        Envelope(payload="business", target="biz.node", trace_id="t1"),
    ]

    with pytest.raises(RuntimeError, match="startup barrier"):
        service.execute_sync(
            runner=runner,
            inputs=inputs,
            run_id="run",
            scenario_id="scenario",
            scenario_scope=scope,  # type: ignore[arg-type]
            enqueue_runner_input=_enqueue,
            replay_root_boundary_outputs=_replay,
            drain_root_boundary_outputs=_drain,
        )


def test_root_loop_service_pumps_leaf_replies_while_waiting_startup_barrier() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    barrier = _Barrier(open_after_loop_calls=2, runner=runner)
    reply_ingress = _ReplyIngress()
    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.ingress",
                "worker_id": "execution.ingress#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.features",
                "worker_id": "execution.features#1",
            },
        ]
    )
    scope = _ScopeWithStartupServices(
        barrier=barrier,
        reply_ingress=reply_ingress,
        state=state,
    )

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    def _replay(**_: object) -> int:
        return 10

    def _drain(**_: object) -> list[object]:
        return []

    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.ingress"}]}}),
        Envelope(payload="business", target="biz.node", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
        replay_root_boundary_outputs=_replay,
        drain_root_boundary_outputs=_drain,
    )

    drained_workers = {worker_id for worker_id, _timeout, _max_items in reply_ingress.calls}
    assert drained_workers == {"execution.ingress#1", "execution.features#1"}


def test_root_loop_service_waits_for_leaf_config_ack_when_readiness_requires_all_groups_ready() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    # Barrier is already open; readiness gate must still pump leaf replies/acks.
    barrier = _Barrier(open_after_loop_calls=0, runner=runner)
    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.ingress",
                "worker_id": "execution.ingress#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.features",
                "worker_id": "execution.features#1",
            },
        ]
    )
    reply_ingress = _ReplyIngressWithAckProgress(
        state=state,
        expected_worker_ids=("execution.ingress#1", "execution.features#1"),
    )
    scope = _ScopeWithStartupServices(
        barrier=barrier,
        reply_ingress=reply_ingress,
        state=state,
    )

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    def _replay(**_: object) -> int:
        return 10

    def _drain(**_: object) -> list[object]:
        return []

    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [{"name": "execution.ingress"}],
                    "readiness": {
                        "enabled": True,
                        "start_work_on_all_groups_ready": True,
                        "readiness_timeout_seconds": 3,
                    },
                }
            }
        ),
        Envelope(payload="business", target="biz.node", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
        replay_root_boundary_outputs=_replay,
        drain_root_boundary_outputs=_drain,
    )

    assert reply_ingress.calls > 0


def test_root_loop_service_pumps_leaf_replies_once_when_startup_barrier_already_open() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    barrier = _Barrier(open_after_loop_calls=0, runner=runner)
    reply_ingress = _ReplyIngress()
    state = _State(
        _events=[
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.ingress",
                "worker_id": "execution.ingress#1",
            },
            {
                "kind": "control_plane.lifecycle.worker_spawned",
                "group_name": "execution.features",
                "worker_id": "execution.features#1",
            },
        ]
    )
    scope = _ScopeWithStartupServices(
        barrier=barrier,
        reply_ingress=reply_ingress,
        state=state,
    )

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    def _replay(**_: object) -> int:
        return 10

    def _drain(**_: object) -> list[object]:
        return []

    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.ingress"}]}}),
        Envelope(payload="business", target="biz.node", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
        replay_root_boundary_outputs=_replay,
        drain_root_boundary_outputs=_drain,
    )

    drained_workers = {worker_id for worker_id, _timeout, _max_items in reply_ingress.calls}
    assert drained_workers == {"execution.ingress#1", "execution.features#1"}
