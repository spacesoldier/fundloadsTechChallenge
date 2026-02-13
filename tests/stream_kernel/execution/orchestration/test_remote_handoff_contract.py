from __future__ import annotations

import pytest

from stream_kernel.execution.orchestration.builder import RuntimeBuildArtifacts, execute_runtime_artifacts
from stream_kernel.execution.orchestration.lifecycle_orchestration import RuntimeWorkerFailedError
from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.platform.services.bootstrap import BootstrapSupervisor
from stream_kernel.platform.services.reply_coordinator import (
    ReplyCoordinatorService,
    legacy_reply_coordinator,
)
from stream_kernel.platform.services.reply_waiter import InMemoryReplyWaiterService, TerminalEvent
from stream_kernel.routing.envelope import Envelope
from stream_kernel.routing.router import RoutingResult
import stream_kernel.execution.orchestration.builder as builder_module


def _runtime_artifacts_for_process_supervisor(
    *,
    runtime: dict[str, object],
    supervisor: object,
    inputs: list[object] | None = None,
    reply_waiter: object | None = None,
) -> RuntimeBuildArtifacts:
    injection = InjectionRegistry()
    injection.register_factory(
        "service",
        BootstrapSupervisor,
        lambda _supervisor=supervisor: _supervisor,
    )
    if reply_waiter is not None:
        injection.register_factory(
            "service",
            ReplyCoordinatorService,
            lambda _waiter=reply_waiter: legacy_reply_coordinator(reply_waiter=_waiter),
        )
    scope = injection.instantiate_for_scenario("scenario")
    return RuntimeBuildArtifacts(
        scenario=type("S", (), {"steps": []})(),
        inputs=list(inputs or []),
        strict=True,
        run_id="run",
        scenario_id="scenario",
        scenario_scope=scope,
        full_context_nodes=set(),
        runtime=runtime,
    )


def _process_supervisor_runtime(*, groups: list[str] | None = None) -> dict[str, object]:
    return {
        "platform": {
            "execution_ipc": {"transport": "tcp_local"},
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [{"name": name} for name in (groups or ["web", "execution.cpu"])],
        },
    }


def test_remote_handoff_contract_requires_group_dispatch_metadata() -> None:
    # HANDOFF-01 (RED): cross-group boundary inputs should contain explicit dispatch-group metadata.
    captured_inputs: list[list[object]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id)
            captured_inputs.append(list(inputs))
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1", reply_to="http:req-1")],
    )
    execute_runtime_artifacts(artifacts)

    assert captured_inputs
    assert all(hasattr(item, "dispatch_group") for item in captured_inputs[0])


def test_remote_handoff_contract_keeps_same_group_workload_local() -> None:
    # HANDOFF-02 (RED): same-group workloads should execute locally without boundary hop.
    events: list[str] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            events.append("start")

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            events.append("ready")
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            events.append("boundary")
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)
            events.append("stop")

    original = builder_module.run_with_sync_runner
    builder_module.run_with_sync_runner = lambda **_kwargs: events.append("runner")
    try:
        artifacts = _runtime_artifacts_for_process_supervisor(
            runtime=_process_supervisor_runtime(groups=["execution.cpu"]),
            supervisor=_Supervisor(),
            inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
        )
        execute_runtime_artifacts(artifacts)
    finally:
        builder_module.run_with_sync_runner = original

    assert "runner" in events
    assert "boundary" not in events


def test_remote_handoff_contract_maps_child_terminal_to_original_trace_id() -> None:
    # HANDOFF-03 (RED): child terminal correlation should map back to original ingress trace id.
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
    waiter.register(trace_id="t1", reply_to="http:req-1", timeout_seconds=30)

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[
                    Envelope(
                        payload=TerminalEvent(status="success", payload={"ok": True}),
                        trace_id="child:t1",
                        target="sink:ignored",
                    )
                ],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1", reply_to="http:req-1")],
        reply_waiter=waiter,
    )
    execute_runtime_artifacts(artifacts)

    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"ok": True})


def test_remote_handoff_contract_requires_explicit_child_failure_category() -> None:
    # HANDOFF-04 (RED): child execution timeout/crash should map to explicit remote-handoff error wording.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            raise TimeoutError("child execution timeout")

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError, match="remote handoff timed out for group"):
        execute_runtime_artifacts(artifacts)


def test_remote_handoff_contract_waits_for_boundary_drain_before_stop() -> None:
    # HANDOFF-05 (RED): supervisor should wait boundary drain acknowledgment before stop.
    events: list[str] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names
            events.append("start")

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            events.append("ready")
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            events.append("boundary")
            return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

        def wait_boundary_drain(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            events.append("wait_boundary_drain")
            return True

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)
            events.append("stop")

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    execute_runtime_artifacts(artifacts)

    assert events.index("wait_boundary_drain") < events.index("stop")


def test_remote_handoff_contract_rejects_local_deliveries_in_boundary_result() -> None:
    # BOUNDARY-CONTRACT-01: execute_boundary must not return local_deliveries on parent boundary path.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[("node.local", {"v": 1})],
                boundary_deliveries=[],
                terminal_outputs=[],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError, match="remote handoff failed for group"):
        execute_runtime_artifacts(artifacts)


def test_remote_handoff_contract_rejects_nested_boundary_deliveries() -> None:
    # BOUNDARY-CONTRACT-02: execute_boundary must not return boundary_deliveries from child boundary result.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[Envelope(payload={"v": 2}, target="child:next", trace_id="t1")],
                terminal_outputs=[],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError, match="remote handoff failed for group"):
        execute_runtime_artifacts(artifacts)


def test_remote_handoff_contract_rejects_non_envelope_terminal_output_items() -> None:
    # BOUNDARY-CONTRACT-03: terminal_outputs channel accepts Envelope items only.
    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[{"status": "success"}],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError, match="remote handoff failed for group"):
        execute_runtime_artifacts(artifacts)


def test_remote_handoff_reply_completion_duplicate_child_terminal_is_deterministic() -> None:
    # HANDOFF-D-01: duplicate child terminal events must keep first completion and increment duplicate counter.
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)
    waiter.register(trace_id="t1", reply_to="http:req-1", timeout_seconds=30)

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[
                    Envelope(
                        payload=TerminalEvent(status="success", payload={"n": 1}),
                        trace_id="child:t1",
                        target="sink:ignored",
                    ),
                    Envelope(
                        payload=TerminalEvent(status="success", payload={"n": 2}),
                        trace_id="child:t1",
                        target="sink:ignored",
                    ),
                ],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1", reply_to="http:req-1")],
        reply_waiter=waiter,
    )
    execute_runtime_artifacts(artifacts)

    assert waiter.poll(trace_id="t1") == TerminalEvent(status="success", payload={"n": 1})
    counters = waiter.diagnostics_counters()
    assert counters["completed"] == 1
    assert counters["duplicate_terminal"] == 1


def test_remote_handoff_reply_completion_late_child_terminal_is_dropped() -> None:
    # HANDOFF-D-02: late child terminal without in-flight waiter should be dropped deterministically.
    waiter = InMemoryReplyWaiterService(now_fn=lambda: 0)

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            return RoutingResult(
                local_deliveries=[],
                boundary_deliveries=[],
                terminal_outputs=[
                    Envelope(
                        payload=TerminalEvent(status="error", error="late"),
                        trace_id="child:t1",
                        target="sink:ignored",
                    )
                ],
            )

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1", reply_to="http:req-1")],
        reply_waiter=waiter,
    )
    execute_runtime_artifacts(artifacts)

    assert waiter.poll(trace_id="t1") is None
    counters = waiter.diagnostics_counters()
    assert counters["completed"] == 0
    assert counters["late_reply_drop"] == 1


def test_remote_handoff_timeout_failure_is_categorized_and_sanitized() -> None:
    # HANDOFF-E-01: timeout failure must map to deterministic timeout category without leaking raw details.
    diagnostics: list[tuple[str, str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            raise TimeoutError("SECRET child timeout details")

        def emit_handoff_failure(self, *, group_name: str, category: str) -> None:
            diagnostics.append((group_name, category))

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError) as exc_info:
        execute_runtime_artifacts(artifacts)

    assert str(exc_info.value) == "remote handoff timed out for group 'execution.cpu'"
    assert "SECRET" not in str(exc_info.value)
    assert diagnostics == [("execution.cpu", "timeout")]


def test_remote_handoff_transport_failure_is_categorized_and_sanitized() -> None:
    # HANDOFF-E-02: transport failure must map to deterministic transport category and sanitized diagnostics.
    diagnostics: list[tuple[str, str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            raise ConnectionError("SECRET transport failure details")

        def emit_handoff_failure(self, *, group_name: str, category: str) -> None:
            diagnostics.append((group_name, category))

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError) as exc_info:
        execute_runtime_artifacts(artifacts)

    assert str(exc_info.value) == "remote handoff transport failed for group 'execution.cpu'"
    assert "SECRET" not in str(exc_info.value)
    assert diagnostics == [("execution.cpu", "transport")]


def test_remote_handoff_primary_failure_is_not_masked_by_shutdown_failure() -> None:
    # HANDOFF-E-03: primary boundary handoff failure must win over shutdown fallback failures.
    diagnostics: list[tuple[str, str]] = []

    class _Supervisor:
        def start_groups(self, group_names: list[str]) -> None:
            _ = group_names

        def wait_ready(self, timeout_seconds: int) -> bool:
            _ = timeout_seconds
            return True

        def execute_boundary(self, *, run, run_id: str, scenario_id: str, inputs: list[object]) -> RoutingResult:
            _ = (run, run_id, scenario_id, inputs)
            raise TimeoutError("child timeout")

        def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
            _ = (graceful_timeout_seconds, drain_inflight)
            raise TimeoutError("stop timeout")

        def force_terminate_groups(self, group_names: list[str]) -> None:
            _ = group_names
            raise RuntimeError("SECRET terminate details")

        def emit_handoff_failure(self, *, group_name: str, category: str) -> None:
            diagnostics.append((group_name, category))

    artifacts = _runtime_artifacts_for_process_supervisor(
        runtime=_process_supervisor_runtime(),
        supervisor=_Supervisor(),
        inputs=[Envelope(payload={"v": 1}, target="n1", trace_id="t1")],
    )
    with pytest.raises(RuntimeWorkerFailedError) as exc_info:
        execute_runtime_artifacts(artifacts)

    assert str(exc_info.value) == "remote handoff timed out for group 'execution.cpu'"
    # timeout category for primary failure + shutdown category for suppressed stop failure.
    assert diagnostics == [("execution.cpu", "timeout"), ("execution.cpu", "shutdown")]
