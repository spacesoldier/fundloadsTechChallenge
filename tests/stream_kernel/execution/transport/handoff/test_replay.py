from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.execution.transport.handoff import replay as replay_module
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _HandoffService:
    external_calls: list[dict[str, object]] = field(default_factory=list)
    completed_calls: int = 0
    inflight: bool = False
    external_result: list[object] = field(default_factory=list)
    completed_result: list[object] = field(default_factory=list)
    last_poll_timeout_seconds: float | None = None
    clear_inflight_on_completed_drain: bool = False
    blocking_inflight: bool | None = None

    def drain_external_deliveries(
        self,
        *,
        envelopes: list[Envelope],
        source_group: str | None = None,
    ) -> list[object]:
        self.external_calls.append(
            {
                "envelopes": list(envelopes),
                "source_group": source_group,
            }
        )
        return list(self.external_result)

    def drain_completed_deliveries(self, *, poll_timeout_seconds: float = 0.0) -> list[object]:
        self.completed_calls += 1
        self.last_poll_timeout_seconds = float(poll_timeout_seconds)
        if self.clear_inflight_on_completed_drain:
            self.inflight = False
        return list(self.completed_result)

    def has_inflight_deliveries(self) -> bool:
        return bool(self.inflight)

    def has_replay_blocking_inflight_deliveries(self) -> bool:
        if self.blocking_inflight is None:
            return bool(self.inflight)
        return bool(self.blocking_inflight)


@dataclass(slots=True)
class _Scope:
    service: object

    def resolve(self, port_type: str, data_type: object) -> object:  # noqa: ARG002
        if port_type != "service":
            raise ValueError("unsupported")
        return self.service


@dataclass(slots=True)
class _SyncRunner:
    external_deliveries: list[Envelope]
    run_calls: list[dict[str, object]] = field(default_factory=list)

    def run_until_stopped(self, *, poll_timeout_seconds: float, idle_timeout_seconds: float | None) -> None:
        self.run_calls.append(
            {
                "poll_timeout_seconds": poll_timeout_seconds,
                "idle_timeout_seconds": idle_timeout_seconds,
            }
        )
        self.external_deliveries = []


def test_drain_root_boundary_handoff_prefers_external_batch_when_present() -> None:
    service = _HandoffService(external_result=[{"ok": 1}], completed_result=["unexpected"])
    scope = _Scope(service=service)

    drained = replay_module.drain_root_boundary_handoff(
        scenario_scope=scope,
        external_deliveries=[Envelope(payload={"x": 1}, target="remote.node", trace_id="t1")],
    )

    assert drained == [{"ok": 1}]
    assert len(service.external_calls) == 1
    assert service.completed_calls == 0


def test_replay_root_boundary_handoff_outputs_sync_requeues_and_runs_until_drained(
    monkeypatch,
) -> None:
    service = _HandoffService(
        external_result=[Envelope(payload={"data": 1}, target="biz.next", trace_id="t-next")],
        inflight=False,
    )
    scope = _Scope(service=service)
    runner = _SyncRunner(
        external_deliveries=[Envelope(payload={"x": 1}, target="remote.node", trace_id="t1")]
    )
    enqueued: list[dict[str, object]] = []

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        enqueued.append(
            {
                "payload": payload,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "index": index,
            }
        )

    monkeypatch.setattr(replay_module, "enqueue_runner_input_sync", _enqueue)

    next_index = replay_module.replay_root_boundary_handoff_outputs_sync(
        runner=runner,  # type: ignore[arg-type]
        scenario_scope=scope,  # type: ignore[arg-type]
        run_id="run",
        scenario_id="scenario",
        start_index=3,
        poll_timeout_seconds=0.01,
        idle_timeout_seconds=0.1,
    )

    assert next_index == 4
    assert len(enqueued) == 1
    assert isinstance(enqueued[0]["payload"], Envelope)
    assert enqueued[0]["index"] == 3
    assert len(runner.run_calls) == 1


def test_replay_root_boundary_handoff_outputs_sync_skips_observability_requeue(
    monkeypatch,
) -> None:
    service = _HandoffService(
        external_result=[Envelope(payload={"obs": 1}, target="system.obs.log_dispatch", trace_id="t-ob")],
        inflight=False,
    )
    scope = _Scope(service=service)
    runner = _SyncRunner(
        external_deliveries=[Envelope(payload={"x": 1}, target="system.obs.log_dispatch", trace_id="t1")]
    )
    enqueued: list[dict[str, object]] = []

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        enqueued.append(
            {
                "payload": payload,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "index": index,
            }
        )

    monkeypatch.setattr(replay_module, "enqueue_runner_input_sync", _enqueue)

    next_index = replay_module.replay_root_boundary_handoff_outputs_sync(
        runner=runner,  # type: ignore[arg-type]
        scenario_scope=scope,  # type: ignore[arg-type]
        run_id="run",
        scenario_id="scenario",
        start_index=3,
        poll_timeout_seconds=0.01,
        idle_timeout_seconds=0.1,
    )

    assert next_index == 3
    assert enqueued == []
    assert len(runner.run_calls) == 0
    assert len(service.external_calls) == 1


def test_replay_root_boundary_handoff_uses_poll_timeout_for_completed_drain() -> None:
    service = _HandoffService(
        external_result=[],
        completed_result=[],
        inflight=True,
        clear_inflight_on_completed_drain=True,
    )
    scope = _Scope(service=service)
    runner = _SyncRunner(external_deliveries=[])

    next_index = replay_module.replay_root_boundary_handoff_outputs_sync(
        runner=runner,  # type: ignore[arg-type]
        scenario_scope=scope,  # type: ignore[arg-type]
        run_id="run",
        scenario_id="scenario",
        start_index=1,
        poll_timeout_seconds=0.017,
        idle_timeout_seconds=0.1,
    )

    assert next_index == 1
    assert service.completed_calls >= 1
    assert service.last_poll_timeout_seconds == 0.001


def test_root_boundary_handoff_has_inflight_prefers_blocking_inflight_method() -> None:
    service = _HandoffService(inflight=True, blocking_inflight=False)
    scope = _Scope(service=service)
    assert replay_module.root_boundary_handoff_has_inflight(scope) is False
