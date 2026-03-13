from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from stream_kernel.execution.orchestration.control_plane.root.leaf_ingress_nodes import (
    ROOT_LEAF_INGRESS_SOURCE_NODE_NAME,
)
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.execution.orchestration.runtime.root_loop_orchestration_service import (
    RootRunnerLoopOrchestrationService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
    RootConsoleLogDispatchService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
    ControlPlaneRootPulse,
)
from stream_kernel.routing.envelope import Envelope


@dataclass(slots=True)
class _SyncRunner:
    allow_external_deliveries: bool = False
    external_deliveries: list[Envelope] = field(default_factory=list)
    nodes: dict[str, object] = field(
        default_factory=lambda: {ROOT_LEAF_INGRESS_SOURCE_NODE_NAME: object()}
    )
    run_calls: int = 0
    loop_calls: int = 0
    last_idle_timeout_seconds: float | None = 0.0

    def run(self) -> None:
        self.run_calls += 1

    def run_until_stopped(self, *, poll_timeout_seconds: float, idle_timeout_seconds: float | None) -> None:  # noqa: ARG002
        self.loop_calls += 1
        self.last_idle_timeout_seconds = idle_timeout_seconds


@dataclass(slots=True)
class _ConsoleDispatch(RootConsoleLogDispatchService):
    messages: list[object] = field(default_factory=list)

    def publish(self, message):  # type: ignore[override]
        self.messages.append(message)
        return True

    def drain(self, *, timeout_seconds: float = 1.0) -> bool:
        _ = timeout_seconds
        return True

    def stop(self, *, drain: bool = True, timeout_seconds: float = 1.0) -> None:
        _ = (drain, timeout_seconds)


@dataclass(slots=True)
class _RunnerControl:
    bound_callbacks: list[object] = field(default_factory=list)
    clear_calls: int = 0

    def bind_runner_stop(self, callback: object) -> None:
        self.bound_callbacks.append(callback)

    def clear_runner_stop(self) -> None:
        self.clear_calls += 1


@dataclass(slots=True)
class _ScopeWithConsoleDispatch:
    console_dispatch: RootConsoleLogDispatchService

    def resolve(self, port_type: str, data_type: object) -> object:  # noqa: ANN401
        if port_type != "service":
            raise ValueError(f"unsupported resolve({port_type!r}, {data_type!r})")
        if data_type is RootConsoleLogDispatchService:
            return self.console_dispatch
        raise ValueError(f"unsupported service contract: {data_type!r}")


@dataclass(slots=True)
class _ScopeWithRunnerControl:
    runner_control: object

    def resolve(self, port_type: str, data_type: object) -> object:  # noqa: ANN401
        if port_type != "service":
            raise ValueError(f"unsupported resolve({port_type!r}, {data_type!r})")
        return self.runner_control


def test_root_loop_service_detects_root_runtime_from_root_pulse() -> None:
    service = RootRunnerLoopOrchestrationService()
    runtime = {"platform": {"process_groups": [{"name": "execution.alpha"}]}}
    inputs = [
        ControlPlaneRootPulse(runtime=runtime),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]

    assert service._root_startup_runtime(inputs) == runtime


def test_root_loop_service_detects_root_runtime_from_root_init_event() -> None:
    service = RootRunnerLoopOrchestrationService()
    runtime = {"platform": {"process_groups": [{"name": "execution.alpha"}]}}
    inputs = [
        Envelope(
            payload=ControlPlaneInitEvent(runtime=runtime),
            target="system.cp.bootstrap_dispatch",
        ),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]

    assert service._root_startup_runtime(inputs) == runtime


def test_root_loop_service_detects_root_runtime_from_pulse_wrapped_in_envelope() -> None:
    service = RootRunnerLoopOrchestrationService()
    runtime = {"platform": {"process_groups": [{"name": "execution.alpha"}]}}
    pulse = ControlPlaneRootPulse(runtime=runtime)
    inputs = [
        Envelope(payload=pulse, target="system.cp.root_bootstrap"),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]

    assert service._root_startup_runtime(inputs) == runtime


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

    runtime = service._root_startup_runtime(inputs)
    poll_timeout_seconds, idle_timeout_seconds = service.resolve_runner_loop_timeouts(runtime)
    assert poll_timeout_seconds == 0.0075
    assert idle_timeout_seconds == 0.25


def test_root_loop_service_executes_sync_root_mode_via_graph_path() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
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

    inputs = [
        ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.alpha"}]}}),
        Envelope(payload=1, target="A", trace_id="t1"),
    ]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=object(),
        enqueue_runner_input=_enqueue,
    )

    assert runner.allow_external_deliveries is False
    assert runner.loop_calls == 1
    assert runner.run_calls == 0
    assert len(enqueued) == 2


def test_root_loop_service_executes_non_root_mode_via_sync_run() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
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

    inputs = [Envelope(payload=1, target="A", trace_id="t1"), Envelope(payload=2, target="B", trace_id="t2")]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=object(),
        enqueue_runner_input=_enqueue,
    )

    assert runner.run_calls == 2
    assert runner.loop_calls == 0
    assert len(enqueued) == 2


def test_root_loop_service_resolves_business_source_targets_and_excludes_system_sources() -> None:
    service = RootRunnerLoopOrchestrationService()
    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [
                        {
                            "name": "execution.ingress",
                            "nodes": [
                                "source:source",
                                "source:system.cp.root_leaf_ingress:execution.ingress#1:control",
                            ],
                        },
                    ],
                }
            }
        ),
        Envelope(
            payload=BootstrapControl(
                target="source:system.cp.root_leaf_ingress:execution.ingress#1:control"
            ),
            target="source:system.cp.root_leaf_ingress:execution.ingress#1:control",
        ),
    ]

    assert service._resolve_source_start_targets_from_runtime(inputs) == ["source:source"]


def test_root_loop_service_emits_verbose_root_logs_when_debug_enabled() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    console = _ConsoleDispatch()
    scope = _ScopeWithConsoleDispatch(console_dispatch=console)

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [{"name": "execution.ingress"}],
                    "debug": {"root_verbose_logging": True},
                }
            }
        ),
    ]

    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
    )

    assert len(console.messages) >= 2
    events = [
        getattr(message, "fields", {}).get("event")
        for message in console.messages
        if hasattr(message, "fields")
    ]
    assert "control_plane.runtime.root_loop_started" in events
    assert "control_plane.runtime.root_loop_finished" in events


def test_root_loop_service_falls_back_to_stderr_when_console_dispatch_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    inputs = [
        ControlPlaneRootPulse(
            runtime={
                "platform": {
                    "process_groups": [{"name": "execution.ingress"}],
                    "debug": {"root_verbose_logging": True},
                }
            }
        ),
    ]

    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=object(),
        enqueue_runner_input=_enqueue,
    )

    captured = capsys.readouterr()
    assert "control-plane root loop started" in captured.err
    assert "control_plane.runtime.root_loop_started" in captured.err


def test_root_loop_service_uses_runner_control_when_available() -> None:
    service = RootRunnerLoopOrchestrationService()
    runner = _SyncRunner()
    runner_control = _RunnerControl()
    scope = _ScopeWithRunnerControl(runner_control=runner_control)

    def _enqueue(
        _runner: object,
        payload: object,
        *,
        run_id: str,
        scenario_id: str,
        index: int,
    ) -> None:
        _ = (payload, run_id, scenario_id, index)

    inputs = [ControlPlaneRootPulse(runtime={"platform": {"process_groups": [{"name": "execution.ingress"}]}})]
    service.execute_sync(
        runner=runner,
        inputs=inputs,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,  # type: ignore[arg-type]
        enqueue_runner_input=_enqueue,
    )

    assert len(runner_control.bound_callbacks) == 1
    assert runner_control.clear_calls == 1
    assert runner.last_idle_timeout_seconds is None
