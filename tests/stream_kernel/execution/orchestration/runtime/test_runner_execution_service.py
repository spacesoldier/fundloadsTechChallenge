from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import stream_kernel.execution.orchestration.runtime.runner_execution_service as runner_execution_module
from stream_kernel.platform.services.runtime.control_plane_events import ControlPlaneRootPulse
from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
    ControlPlaneStartupBarrierService,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _Scope:
    closed: bool = False

    def close(self) -> None:
        self.closed = True

    def resolve(self, _port_type: str, _data_type: object) -> object:
        raise ValueError("resolve is not configured for this scope")


@dataclass(slots=True)
class _Barrier(ControlPlaneStartupBarrierService):
    open_after_loop_calls: int | None = None
    runner: "_RunnerWithLoopCounter | None" = None

    def mark_discovery_completed(self, *, runtime: dict[str, object]) -> bool:  # noqa: ARG002
        return False

    def mark_config_completed(self, *, runtime: dict[str, object]) -> bool:  # noqa: ARG002
        return False

    def is_open(self) -> bool:
        if self.open_after_loop_calls is None or self.runner is None:
            return False
        return self.runner.loop_calls >= self.open_after_loop_calls

    def reset(self) -> None:
        return None


@dataclass(slots=True)
class _ScopeWithBarrier(_Scope):
    barrier: ControlPlaneStartupBarrierService | None = None

    def resolve(self, port_type: str, _data_type: object) -> object:
        if port_type == "service" and self.barrier is not None:
            return self.barrier
        raise ValueError("resolve is not configured for this scope")


@dataclass(slots=True)
class _LoopRecorder:
    sync_calls: list[dict[str, object]]
    async_calls: list[dict[str, object]]
    fail_sync: bool = False

    def execute_sync(self, **kwargs: object) -> None:
        self.sync_calls.append(dict(kwargs))
        if self.fail_sync:
            raise RuntimeError("sync loop failed")

    def execute_async(self, **kwargs: object) -> None:
        self.async_calls.append(dict(kwargs))


@dataclass(slots=True)
class _RunnerStub:
    nodes: dict[str, object]
    full_context_nodes: set[str]
    ordered_sink_mode: str
    work_queue: object = "default-queue"
    allow_external_deliveries: bool = False
    external_deliveries: list[Envelope] = None  # type: ignore[assignment]
    ended: bool = False

    def on_run_end(self) -> None:
        self.ended = True


@dataclass(slots=True)
class _RunnerWithLoopCounter(_RunnerStub):
    loop_calls: int = 0

    def run_until_stopped(self, *, poll_timeout_seconds: float, idle_timeout_seconds: float | None) -> None:  # noqa: ARG002
        self.loop_calls += 1


def test_run_with_sync_runner_uses_runtime_service_and_closes_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[dict[str, object]] = []
    loop = _LoopRecorder(sync_calls=sync_calls, async_calls=[])
    apply_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner_execution_module, "SyncRunner", _RunnerStub)
    monkeypatch.setattr(
        runner_execution_module,
        "RootRunnerLoopOrchestrationService",
        lambda: loop,
    )
    monkeypatch.setattr(
        runner_execution_module,
        "apply_injection",
        lambda runner, scope, strict: apply_calls.append(
            {"runner": runner, "scope": scope, "strict": strict}
        ),
    )

    scope = _Scope()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="n1", step=lambda payload, ctx: [])]
    )
    inputs = [Envelope(payload={"k": "v"}, target="n1", trace_id="trace")]

    runner_execution_module.run_with_sync_runner(
        scenario=scenario,
        inputs=inputs,
        strict=True,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
        full_context_nodes={"n1"},
    )

    assert len(sync_calls) == 1
    call = sync_calls[0]
    assert call["inputs"] == inputs
    assert call["run_id"] == "run-1"
    assert call["scenario_id"] == "scenario-1"
    assert call["scenario_scope"] is scope
    runner = call["runner"]
    assert isinstance(runner, _RunnerStub)
    assert runner.ended is True
    assert scope.closed is True
    assert len(apply_calls) == 1
    assert apply_calls[0]["runner"] is runner
    assert apply_calls[0]["scope"] is scope
    assert apply_calls[0]["strict"] is True


def test_run_with_sync_runner_overrides_queue_qualifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[dict[str, object]] = []
    loop = _LoopRecorder(sync_calls=sync_calls, async_calls=[])
    queues: list[dict[str, object]] = []
    queue_instance = object()

    monkeypatch.setattr(runner_execution_module, "SyncRunner", _RunnerStub)
    monkeypatch.setattr(
        runner_execution_module,
        "RootRunnerLoopOrchestrationService",
        lambda: loop,
    )
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_execution_module,
        "inject",
        SimpleNamespace(
            queue=lambda _token, *, qualifier: queues.append({"qualifier": qualifier}) or queue_instance
        ),
    )

    scope = _Scope()
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="n1", step=lambda payload, ctx: [])])

    runner_execution_module.run_with_sync_runner(
        scenario=scenario,
        inputs=[],
        strict=True,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
        queue_qualifier="execution.custom",
    )

    assert queues == [{"qualifier": "execution.custom"}]
    assert len(sync_calls) == 1
    assert sync_calls[0]["runner"].work_queue is queue_instance


def test_run_with_sync_runner_finalizes_scope_when_loop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[dict[str, object]] = []
    loop = _LoopRecorder(sync_calls=sync_calls, async_calls=[], fail_sync=True)

    monkeypatch.setattr(runner_execution_module, "SyncRunner", _RunnerStub)
    monkeypatch.setattr(
        runner_execution_module,
        "RootRunnerLoopOrchestrationService",
        lambda: loop,
    )
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)

    scope = _Scope()
    scenario = SimpleNamespace(steps=[SimpleNamespace(name="n1", step=lambda payload, ctx: [])])

    with pytest.raises(RuntimeError, match="sync loop failed"):
        runner_execution_module.run_with_sync_runner(
            scenario=scenario,
            inputs=[],
            strict=True,
            run_id="run-1",
            scenario_id="scenario-1",
            scenario_scope=scope,
        )

    assert len(sync_calls) == 1
    assert sync_calls[0]["runner"].ended is True
    assert scope.closed is True


def test_run_with_async_runner_uses_runtime_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_calls: list[dict[str, object]] = []
    loop = _LoopRecorder(sync_calls=[], async_calls=async_calls)
    apply_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner_execution_module, "AsyncRunner", _RunnerStub)
    monkeypatch.setattr(
        runner_execution_module,
        "RootRunnerLoopOrchestrationService",
        lambda: loop,
    )
    monkeypatch.setattr(
        runner_execution_module,
        "apply_injection",
        lambda runner, scope, strict: apply_calls.append(
            {"runner": runner, "scope": scope, "strict": strict}
        ),
    )

    scope = _Scope()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="n1", step=lambda payload, ctx: [])]
    )

    runner_execution_module.run_with_async_runner(
        scenario=scenario,
        inputs=[],
        strict=False,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
    )

    assert len(async_calls) == 1
    runner = async_calls[0]["runner"]
    assert isinstance(runner, _RunnerStub)
    assert runner.ended is True
    assert scope.closed is True
    assert len(apply_calls) == 1
    assert apply_calls[0]["runner"] is runner
    assert apply_calls[0]["scope"] is scope
    assert apply_calls[0]["strict"] is False


def test_run_with_async_runner_defers_business_inputs_until_startup_barrier_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[object] = []
    runner_refs: list[_RunnerWithLoopCounter] = []
    barrier = _Barrier(open_after_loop_calls=2)

    def _async_runner_factory(**kwargs: object) -> _RunnerWithLoopCounter:
        runner = _RunnerWithLoopCounter(**kwargs)
        barrier.runner = runner
        runner_refs.append(runner)
        return runner

    monkeypatch.setattr(runner_execution_module, "AsyncRunner", _async_runner_factory)
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_execution_module,
        "enqueue_runner_input_async",
        lambda _runner, payload, **_kwargs: enqueued.append(payload),
    )
    monkeypatch.setattr(
        runner_execution_module.transport_handoff_replay,
        "replay_root_boundary_handoff_outputs_async",
        lambda **kwargs: int(kwargs["start_index"]),
    )
    monkeypatch.setattr(
        runner_execution_module.transport_handoff_replay,
        "drain_root_boundary_handoff",
        lambda **_kwargs: [],
    )

    scope = _ScopeWithBarrier()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="system.cp.root_bootstrap", step=lambda payload, ctx: [])]
    )
    root_pulse = ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}})
    business = Envelope(payload={"kind": "biz"}, target="biz.node", trace_id="trace")
    scope.barrier = barrier

    runner_execution_module.run_with_async_runner(
        scenario=scenario,
        inputs=[root_pulse, business],
        strict=True,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
        full_context_nodes={"system.cp.root_bootstrap"},
    )

    assert len(runner_refs) == 1
    assert isinstance(enqueued[0], ControlPlaneRootPulse)
    assert isinstance(enqueued[1], Envelope)
    assert runner_refs[0].loop_calls == 3
    assert scope.closed is True


def test_run_with_async_runner_raises_when_startup_barrier_timeout_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_refs: list[_RunnerWithLoopCounter] = []
    barrier = _Barrier(open_after_loop_calls=None)

    def _async_runner_factory(**kwargs: object) -> _RunnerWithLoopCounter:
        runner = _RunnerWithLoopCounter(**kwargs)
        barrier.runner = runner
        runner_refs.append(runner)
        return runner

    monkeypatch.setattr(runner_execution_module, "AsyncRunner", _async_runner_factory)
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner_execution_module, "enqueue_runner_input_async", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_execution_module.transport_handoff_replay,
        "replay_root_boundary_handoff_outputs_async",
        lambda **kwargs: int(kwargs["start_index"]),
    )
    monkeypatch.setattr(
        runner_execution_module.transport_handoff_replay,
        "drain_root_boundary_handoff",
        lambda **_kwargs: [],
    )

    scope = _ScopeWithBarrier()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="system.cp.root_bootstrap", step=lambda payload, ctx: [])]
    )
    root_pulse = ControlPlaneRootPulse(
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
    )
    business = Envelope(payload={"kind": "biz"}, target="biz.node", trace_id="trace")
    scope.barrier = barrier

    with pytest.raises(RuntimeError, match="startup barrier"):
        runner_execution_module.run_with_async_runner(
            scenario=scenario,
            inputs=[root_pulse, business],
            strict=True,
            run_id="run-1",
            scenario_id="scenario-1",
            scenario_scope=scope,
            full_context_nodes={"system.cp.root_bootstrap"},
        )

    assert len(runner_refs) == 1
    assert runner_refs[0].ended is True
    assert scope.closed is True
