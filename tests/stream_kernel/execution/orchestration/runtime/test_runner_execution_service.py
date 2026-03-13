from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import stream_kernel.execution.orchestration.runtime.runner_execution_service as runner_execution_module
from stream_kernel.platform.services.runtime.control_plane_events import ControlPlaneInitEvent
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _Scope:
    closed: bool = False

    def close(self) -> None:
        self.closed = True

    def resolve(self, _port_type: str, _data_type: object) -> object:
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
    run_id: str
    scenario_id: str
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


def test_run_with_async_runner_enqueues_all_inputs_in_root_control_plane_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[object] = []
    runner_refs: list[_RunnerWithLoopCounter] = []

    def _async_runner_factory(**kwargs: object) -> _RunnerWithLoopCounter:
        runner = _RunnerWithLoopCounter(**kwargs)
        runner_refs.append(runner)
        return runner

    monkeypatch.setattr(runner_execution_module, "AsyncRunner", _async_runner_factory)
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_execution_module,
        "enqueue_runner_input_async",
        lambda _runner, payload, **_kwargs: enqueued.append(payload),
    )

    scope = _Scope()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="system.cp.root_bootstrap", step=lambda payload, ctx: [])]
    )
    root_init = Envelope(
        payload=ControlPlaneInitEvent(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}}),
        target="system.cp.bootstrap_dispatch",
    )
    business = Envelope(payload={"kind": "biz"}, target="biz.node", trace_id="trace")

    runner_execution_module.run_with_async_runner(
        scenario=scenario,
        inputs=[root_init, business],
        strict=True,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
        full_context_nodes={"system.cp.root_bootstrap"},
    )

    assert len(runner_refs) == 1
    assert len(enqueued) == 2
    assert isinstance(enqueued[0], Envelope)
    assert isinstance(enqueued[0].payload, ControlPlaneInitEvent)
    assert enqueued[0].target == "system.cp.bootstrap_dispatch"
    assert isinstance(enqueued[1], Envelope)
    assert enqueued[1].target == "biz.node"
    assert runner_refs[0].loop_calls == 1
    assert scope.closed is True


def test_run_with_async_runner_does_not_use_startup_barrier_timeout_in_root_control_plane_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_refs: list[_RunnerWithLoopCounter] = []

    def _async_runner_factory(**kwargs: object) -> _RunnerWithLoopCounter:
        runner = _RunnerWithLoopCounter(**kwargs)
        runner_refs.append(runner)
        return runner

    monkeypatch.setattr(runner_execution_module, "AsyncRunner", _async_runner_factory)
    monkeypatch.setattr(runner_execution_module, "apply_injection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner_execution_module, "enqueue_runner_input_async", lambda *_args, **_kwargs: None)

    scope = _Scope()
    scenario = SimpleNamespace(
        steps=[SimpleNamespace(name="system.cp.root_bootstrap", step=lambda payload, ctx: [])]
    )
    root_init = Envelope(
        payload=ControlPlaneInitEvent(
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
        target="system.cp.bootstrap_dispatch",
    )
    business = Envelope(payload={"kind": "biz"}, target="biz.node", trace_id="trace")

    runner_execution_module.run_with_async_runner(
        scenario=scenario,
        inputs=[root_init, business],
        strict=True,
        run_id="run-1",
        scenario_id="scenario-1",
        scenario_scope=scope,
        full_context_nodes={"system.cp.root_bootstrap"},
    )

    assert len(runner_refs) == 1
    assert runner_refs[0].loop_calls == 1
    assert runner_refs[0].ended is True
    assert scope.closed is True
