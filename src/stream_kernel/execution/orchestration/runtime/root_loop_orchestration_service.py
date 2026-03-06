from __future__ import annotations

import time
import sys
from collections.abc import Callable
from dataclasses import dataclass

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.source_ingress import BootstrapControl
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneRootPulse,
    ControlPlaneShutdownReadyEvent,
    ControlPlaneStartWorkEvent,
)
from stream_kernel.routing.envelope import Envelope

EnqueueRunnerInput = Callable[
    [object, object],
    None,
]


@service(name="root_runner_loop_orchestration_service")
@dataclass(slots=True)
class RootRunnerLoopOrchestrationService:
    default_poll_timeout_seconds: float = 0.01
    default_idle_timeout_seconds: float | None = 0.1
    default_startup_barrier_timeout_seconds: float | None = 5.0

    def has_root_control_plane_pulse(self, inputs: list[object] | tuple[object, ...] | object) -> bool:
        if not isinstance(inputs, list | tuple):
            return False
        return any(isinstance(item, ControlPlaneRootPulse) for item in inputs)

    def split_root_control_plane_inputs(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> tuple[list[tuple[int, object]], list[tuple[int, object]]]:
        if not isinstance(inputs, list | tuple):
            return ([], [])
        startup: list[tuple[int, object]] = []
        deferred: list[tuple[int, object]] = []
        for index, payload in enumerate(inputs, start=1):
            if isinstance(payload, ControlPlaneRootPulse):
                startup.append((index, payload))
                continue
            deferred.append((index, payload))
        return (startup, deferred)

    @staticmethod
    def _extract_source_start_targets_from_deferred_inputs(
        deferred_inputs: list[tuple[int, object]],
    ) -> tuple[list[str], list[tuple[int, object]]]:
        source_targets: list[str] = []
        filtered: list[tuple[int, object]] = []
        for index, payload in deferred_inputs:
            raw = payload.payload if isinstance(payload, Envelope) else payload
            if isinstance(raw, BootstrapControl):
                source_targets.append(raw.target)
                continue
            filtered.append((index, payload))
        return (source_targets, filtered)

    def resolve_runner_loop_timeouts(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> tuple[float, float | None]:
        poll_timeout_seconds = self.default_poll_timeout_seconds
        idle_timeout_seconds: float | None = self.default_idle_timeout_seconds
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return poll_timeout_seconds, idle_timeout_seconds
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return poll_timeout_seconds, idle_timeout_seconds
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return poll_timeout_seconds, idle_timeout_seconds
        runner_loop = platform.get("runner_loop", {})
        if runner_loop is None:
            return poll_timeout_seconds, idle_timeout_seconds
        if not isinstance(runner_loop, dict):
            raise ValueError("runtime.platform.runner_loop must be a mapping when provided")
        if "poll_timeout_ms" in runner_loop:
            poll_timeout_seconds = self._require_positive_milliseconds(
                runner_loop["poll_timeout_ms"],
                "runtime.platform.runner_loop.poll_timeout_ms",
            ) / 1000.0
        if "idle_timeout_ms" in runner_loop:
            idle_timeout_seconds = self._optional_positive_milliseconds_to_seconds(
                runner_loop["idle_timeout_ms"],
                "runtime.platform.runner_loop.idle_timeout_ms",
            )
        return poll_timeout_seconds, idle_timeout_seconds

    def resolve_startup_barrier_timeout_seconds(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> float | None:
        timeout_seconds = self.default_startup_barrier_timeout_seconds
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return timeout_seconds
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return timeout_seconds
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return timeout_seconds
        runner_loop = platform.get("runner_loop", {})
        if runner_loop is None:
            return timeout_seconds
        if not isinstance(runner_loop, dict):
            raise ValueError("runtime.platform.runner_loop must be a mapping when provided")
        if "startup_barrier_timeout_ms" in runner_loop:
            timeout_seconds = self._optional_positive_milliseconds_to_seconds(
                runner_loop["startup_barrier_timeout_ms"],
                "runtime.platform.runner_loop.startup_barrier_timeout_ms",
            )
        return timeout_seconds

    def execute_sync(
        self,
        *,
        runner: object,
        inputs: list[object] | tuple[object, ...],
        run_id: str,
        scenario_id: str,
        scenario_scope: ScenarioScope,
        enqueue_runner_input: Callable[..., None],
        replay_root_boundary_outputs: Callable[..., int],
        drain_root_boundary_outputs: Callable[..., list[object]],
    ) -> None:
        root_control_plane_mode = self.has_root_control_plane_pulse(inputs)
        root_verbose_logging = self._resolve_root_verbose_logging(inputs)
        if root_control_plane_mode:
            setattr(runner, "allow_external_deliveries", True)
            setattr(runner, "external_deliveries", [])
            poll_timeout_seconds, idle_timeout_seconds = self.resolve_runner_loop_timeouts(inputs)
            startup_barrier_timeout_seconds = self.resolve_startup_barrier_timeout_seconds(inputs)
            startup_inputs, deferred_inputs = self.split_root_control_plane_inputs(inputs)
            source_start_targets, deferred_inputs = self._extract_source_start_targets_from_deferred_inputs(
                deferred_inputs
            )
            if not source_start_targets:
                source_start_targets = self._resolve_source_start_targets_from_runtime(inputs)
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root loop started",
                fields={
                    "event": "control_plane.runtime.root_loop_started",
                    "startup_inputs": len(startup_inputs),
                    "deferred_inputs": len(deferred_inputs),
                    "poll_timeout_seconds": poll_timeout_seconds,
                    "idle_timeout_seconds": idle_timeout_seconds,
                    "startup_barrier_timeout_seconds": startup_barrier_timeout_seconds,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            next_replay_index = max((index for index, _payload in startup_inputs + deferred_inputs), default=0) + 1
            deferred_runtime_inputs = list(deferred_inputs)
            should_wait_for_readiness, _readiness_timeout, _fail_on_timeout = self._resolve_readiness_wait_settings(inputs)
            if source_start_targets or should_wait_for_readiness:
                deferred_runtime_inputs.insert(
                    0,
                    (
                        next_replay_index,
                        ControlPlaneStartWorkEvent(source_targets=tuple(source_start_targets)),
                    ),
                )
                next_replay_index += 1
            for index, payload in startup_inputs:
                enqueue_runner_input(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=index,
                )
            runner.run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._wait_for_startup_barrier_open(
                runner=runner,
                scenario_scope=scenario_scope,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                startup_barrier_timeout_seconds=startup_barrier_timeout_seconds,
                verbose_logging=root_verbose_logging,
            )
            self._wait_for_leaf_readiness_if_required(
                runner=runner,
                scenario_scope=scenario_scope,
                inputs=inputs,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                verbose_logging=root_verbose_logging,
            )
            self._configure_shutdown_readiness_expected_groups(
                scenario_scope=scenario_scope,
                inputs=inputs,
            )
            replay_started = time.monotonic()
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane boundary replay started",
                fields={
                    "event": "control_plane.runtime.replay_started",
                    "phase": "startup",
                    "start_index": next_replay_index,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            next_replay_index = replay_root_boundary_outputs(
                runner=runner,
                scenario_scope=scenario_scope,
                run_id=run_id,
                scenario_id=scenario_id,
                start_index=next_replay_index,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane boundary replay finished",
                fields={
                    "event": "control_plane.runtime.replay_finished",
                    "phase": "startup",
                    "next_replay_index": next_replay_index,
                    "duration_seconds": round(max(0.0, time.monotonic() - replay_started), 6),
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            if deferred_runtime_inputs:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane deferred inputs started",
                    fields={
                        "event": "control_plane.runtime.deferred_inputs_started",
                        "count": len(deferred_runtime_inputs),
                        "source_start_targets": list(source_start_targets),
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                for index, payload in deferred_runtime_inputs:
                    enqueue_runner_input(
                        runner,
                        payload,
                        run_id=run_id,
                        scenario_id=scenario_id,
                        index=index,
                    )
                runner.run_until_stopped(
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
                )
                replay_started = time.monotonic()
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane boundary replay started",
                    fields={
                        "event": "control_plane.runtime.replay_started",
                        "phase": "deferred",
                        "start_index": next_replay_index,
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                _ = replay_root_boundary_outputs(
                    runner=runner,
                    scenario_scope=scenario_scope,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    start_index=next_replay_index,
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
                )
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane boundary replay finished",
                    fields={
                        "event": "control_plane.runtime.replay_finished",
                        "phase": "deferred",
                        "next_replay_index": next_replay_index,
                        "duration_seconds": round(max(0.0, time.monotonic() - replay_started), 6),
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                if source_start_targets:
                    next_replay_index = self._settle_after_start_work(
                        runner=runner,
                        scenario_scope=scenario_scope,
                        run_id=run_id,
                        scenario_id=scenario_id,
                        inputs=inputs,
                        source_start_targets=tuple(source_start_targets),
                        next_replay_index=next_replay_index,
                        replay_root_boundary_outputs=replay_root_boundary_outputs,
                        poll_timeout_seconds=poll_timeout_seconds,
                        idle_timeout_seconds=idle_timeout_seconds,
                        verbose_logging=root_verbose_logging,
                    )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root loop finished",
                fields={
                    "event": "control_plane.runtime.root_loop_finished",
                    "next_replay_index": next_replay_index,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
        else:
            for index, payload in enumerate(inputs, start=1):
                enqueue_runner_input(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=index,
                )
                runner.run()
        external_deliveries = getattr(runner, "external_deliveries", None)
        if root_control_plane_mode and isinstance(external_deliveries, list) and external_deliveries:
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root external deliveries drain started",
                fields={
                    "event": "control_plane.runtime.external_drain_started",
                    "count": len(external_deliveries),
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            drain_root_boundary_outputs(
                scenario_scope=scenario_scope,
                external_deliveries=list(external_deliveries),
            )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root external deliveries drain finished",
                fields={
                    "event": "control_plane.runtime.external_drain_finished",
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )

    def execute_async(
        self,
        *,
        runner: object,
        inputs: list[object] | tuple[object, ...],
        run_id: str,
        scenario_id: str,
        scenario_scope: ScenarioScope,
        enqueue_runner_input: Callable[..., None],
        replay_root_boundary_outputs: Callable[..., int],
        drain_root_boundary_outputs: Callable[..., list[object]],
    ) -> None:
        root_control_plane_mode = self.has_root_control_plane_pulse(inputs)
        root_verbose_logging = self._resolve_root_verbose_logging(inputs)
        if root_control_plane_mode:
            setattr(runner, "allow_external_deliveries", True)
            setattr(runner, "external_deliveries", [])
            poll_timeout_seconds, idle_timeout_seconds = self.resolve_runner_loop_timeouts(inputs)
            startup_barrier_timeout_seconds = self.resolve_startup_barrier_timeout_seconds(inputs)
            startup_inputs, deferred_inputs = self.split_root_control_plane_inputs(inputs)
            source_start_targets, deferred_inputs = self._extract_source_start_targets_from_deferred_inputs(
                deferred_inputs
            )
            if not source_start_targets:
                source_start_targets = self._resolve_source_start_targets_from_runtime(inputs)
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root loop started",
                fields={
                    "event": "control_plane.runtime.root_loop_started",
                    "startup_inputs": len(startup_inputs),
                    "deferred_inputs": len(deferred_inputs),
                    "poll_timeout_seconds": poll_timeout_seconds,
                    "idle_timeout_seconds": idle_timeout_seconds,
                    "startup_barrier_timeout_seconds": startup_barrier_timeout_seconds,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            next_replay_index = max((index for index, _payload in startup_inputs + deferred_inputs), default=0) + 1
            deferred_runtime_inputs = list(deferred_inputs)
            should_wait_for_readiness, _readiness_timeout, _fail_on_timeout = self._resolve_readiness_wait_settings(inputs)
            if source_start_targets or should_wait_for_readiness:
                deferred_runtime_inputs.insert(
                    0,
                    (
                        next_replay_index,
                        ControlPlaneStartWorkEvent(source_targets=tuple(source_start_targets)),
                    ),
                )
                next_replay_index += 1
            for index, payload in startup_inputs:
                enqueue_runner_input(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=index,
                )
            runner.run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._wait_for_startup_barrier_open(
                runner=runner,
                scenario_scope=scenario_scope,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                startup_barrier_timeout_seconds=startup_barrier_timeout_seconds,
                verbose_logging=root_verbose_logging,
            )
            self._wait_for_leaf_readiness_if_required(
                runner=runner,
                scenario_scope=scenario_scope,
                inputs=inputs,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                verbose_logging=root_verbose_logging,
            )
            self._configure_shutdown_readiness_expected_groups(
                scenario_scope=scenario_scope,
                inputs=inputs,
            )
            replay_started = time.monotonic()
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane boundary replay started",
                fields={
                    "event": "control_plane.runtime.replay_started",
                    "phase": "startup",
                    "start_index": next_replay_index,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            next_replay_index = replay_root_boundary_outputs(
                runner=runner,
                scenario_scope=scenario_scope,
                run_id=run_id,
                scenario_id=scenario_id,
                start_index=next_replay_index,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane boundary replay finished",
                fields={
                    "event": "control_plane.runtime.replay_finished",
                    "phase": "startup",
                    "next_replay_index": next_replay_index,
                    "duration_seconds": round(max(0.0, time.monotonic() - replay_started), 6),
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            if deferred_runtime_inputs:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane deferred inputs started",
                    fields={
                        "event": "control_plane.runtime.deferred_inputs_started",
                        "count": len(deferred_runtime_inputs),
                        "source_start_targets": list(source_start_targets),
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                for index, payload in deferred_runtime_inputs:
                    enqueue_runner_input(
                        runner,
                        payload,
                        run_id=run_id,
                        scenario_id=scenario_id,
                        index=index,
                    )
                runner.run_until_stopped(
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
                )
                replay_started = time.monotonic()
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane boundary replay started",
                    fields={
                        "event": "control_plane.runtime.replay_started",
                        "phase": "deferred",
                        "start_index": next_replay_index,
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                _ = replay_root_boundary_outputs(
                    runner=runner,
                    scenario_scope=scenario_scope,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    start_index=next_replay_index,
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
                )
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane boundary replay finished",
                    fields={
                        "event": "control_plane.runtime.replay_finished",
                        "phase": "deferred",
                        "next_replay_index": next_replay_index,
                        "duration_seconds": round(max(0.0, time.monotonic() - replay_started), 6),
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
                if source_start_targets:
                    next_replay_index = self._settle_after_start_work(
                        runner=runner,
                        scenario_scope=scenario_scope,
                        run_id=run_id,
                        scenario_id=scenario_id,
                        inputs=inputs,
                        source_start_targets=tuple(source_start_targets),
                        next_replay_index=next_replay_index,
                        replay_root_boundary_outputs=replay_root_boundary_outputs,
                        poll_timeout_seconds=poll_timeout_seconds,
                        idle_timeout_seconds=idle_timeout_seconds,
                        verbose_logging=root_verbose_logging,
                    )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root loop finished",
                fields={
                    "event": "control_plane.runtime.root_loop_finished",
                    "next_replay_index": next_replay_index,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
        else:
            for index, payload in enumerate(inputs, start=1):
                enqueue_runner_input(
                    runner,
                    payload,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    index=index,
                )
                runner.run()
        external_deliveries = getattr(runner, "external_deliveries", None)
        if root_control_plane_mode and isinstance(external_deliveries, list) and external_deliveries:
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root external deliveries drain started",
                fields={
                    "event": "control_plane.runtime.external_drain_started",
                    "count": len(external_deliveries),
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            drain_root_boundary_outputs(
                scenario_scope=scenario_scope,
                external_deliveries=list(external_deliveries),
            )
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root external deliveries drain finished",
                fields={
                    "event": "control_plane.runtime.external_drain_finished",
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )

    def _find_control_plane_root_pulse(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> ControlPlaneRootPulse | None:
        if not isinstance(inputs, list | tuple):
            return None
        for item in inputs:
            if isinstance(item, ControlPlaneRootPulse):
                return item
        return None

    def _resolve_source_start_targets_from_runtime(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> list[str]:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return []
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return []
        platform = runtime.get("platform")
        if not isinstance(platform, dict):
            return []
        groups = platform.get("process_groups")
        if not isinstance(groups, list):
            return []
        targets: list[str] = []
        seen: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                continue
            nodes = group.get("nodes")
            if not isinstance(nodes, list):
                continue
            for node_name in nodes:
                if not isinstance(node_name, str) or not node_name.startswith("source:"):
                    continue
                if node_name in seen:
                    continue
                seen.add(node_name)
                targets.append(node_name)
        return targets

    def _wait_for_startup_barrier_open(
        self,
        *,
        runner: object,
        scenario_scope: ScenarioScope,
        poll_timeout_seconds: float,
        idle_timeout_seconds: float | None,
        startup_barrier_timeout_seconds: float | None,
        verbose_logging: bool = False,
    ) -> None:
        barrier = self._resolve_startup_barrier_service(scenario_scope)
        if barrier is None:
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="warning",
                message="control-plane startup barrier service is unavailable",
                fields={"event": "control_plane.runtime.startup_barrier_unavailable"},
                verbose_only=True,
                verbose_enabled=verbose_logging,
            )
            return
        is_open = getattr(barrier, "is_open", None)
        if not callable(is_open):
            return
        # Always pump startup replies at least once, even when the barrier is
        # already open. Leaf hello/config ack messages can arrive before this
        # wait phase starts and must be drained to unblock startup handshake.
        self._pump_root_startup_replies(
            scenario_scope=scenario_scope,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        if bool(is_open()):
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane startup barrier already open",
                fields={"event": "control_plane.runtime.startup_barrier_open"},
                verbose_only=True,
                verbose_enabled=verbose_logging,
            )
            return
        self._emit_root_log(
            scenario_scope=scenario_scope,
            level="debug",
            message="control-plane waiting for startup barrier",
            fields={
                "event": "control_plane.runtime.startup_barrier_wait_started",
                "timeout_seconds": startup_barrier_timeout_seconds,
            },
            verbose_only=True,
            verbose_enabled=verbose_logging,
        )
        deadline = (
            time.monotonic() + startup_barrier_timeout_seconds
            if startup_barrier_timeout_seconds is not None
            else None
        )
        run_until_stopped = getattr(runner, "run_until_stopped", None)
        if not callable(run_until_stopped):
            raise RuntimeError("root runner loop expected run_until_stopped for startup barrier wait")
        loops = 0
        while not bool(is_open()):
            loops += 1
            self._pump_root_startup_replies(
                scenario_scope=scenario_scope,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._pump_root_startup_replies(
                scenario_scope=scenario_scope,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if bool(is_open()):
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane startup barrier opened",
                    fields={
                        "event": "control_plane.runtime.startup_barrier_open",
                        "loops": loops,
                    },
                    verbose_only=True,
                    verbose_enabled=verbose_logging,
                )
                return
            if verbose_logging and loops % 50 == 0:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane startup barrier pending",
                    fields={
                        "event": "control_plane.runtime.startup_barrier_pending",
                        "loops": loops,
                    },
                    verbose_only=True,
                    verbose_enabled=True,
                )
            if deadline is not None and time.monotonic() >= deadline:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="error",
                    message="control-plane startup barrier timeout",
                    fields={"event": "control_plane.runtime.startup_barrier_timeout"},
                )
                raise RuntimeError("control-plane startup barrier remained closed within timeout")

    @staticmethod
    def _resolve_startup_barrier_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.platform.services.runtime.control_plane_startup_barrier import (
            ControlPlaneStartupBarrierService,
        )

        try:
            resolved = resolve("service", ControlPlaneStartupBarrierService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneStartupBarrierService):
            return resolved
        if callable(getattr(resolved, "is_open", None)):
            return resolved
        return None

    @staticmethod
    def _pump_root_startup_replies(
        *,
        scenario_scope: ScenarioScope,
        poll_timeout_seconds: float,
    ) -> None:
        ingress = RootRunnerLoopOrchestrationService._resolve_root_reply_ingress_service(scenario_scope)
        if ingress is None:
            return
        worker_ids = RootRunnerLoopOrchestrationService._resolve_spawned_worker_ids(scenario_scope)
        if not worker_ids:
            return
        for index, worker_id in enumerate(worker_ids):
            timeout = max(0.0, float(poll_timeout_seconds)) if index == 0 else 0.0
            try:
                ingress.drain_worker_replies(
                    worker_id=worker_id,
                    timeout_seconds=timeout,
                    max_items=64,
                )
            except Exception:
                continue

    def _wait_for_leaf_readiness_if_required(
        self,
        *,
        runner: object,
        scenario_scope: ScenarioScope,
        inputs: list[object] | tuple[object, ...] | object,
        poll_timeout_seconds: float,
        idle_timeout_seconds: float | None,
        verbose_logging: bool = False,
    ) -> None:
        should_wait, timeout_seconds, fail_on_timeout = self._resolve_readiness_wait_settings(inputs)
        if not should_wait:
            return
        ingress = self._resolve_root_reply_ingress_service(scenario_scope)
        state = self._resolve_control_plane_state_service(scenario_scope)
        if ingress is None or state is None:
            return
        expected_worker_ids = self._resolve_expected_worker_ids_for_readiness(state.events())
        if not expected_worker_ids:
            return
        self._emit_root_log(
            scenario_scope=scenario_scope,
            level="debug",
            message="control-plane waiting for leaf readiness",
            fields={
                "event": "control_plane.runtime.readiness_wait_started",
                "expected_workers": expected_worker_ids,
                "timeout_seconds": timeout_seconds,
                "fail_on_timeout": fail_on_timeout,
            },
            verbose_only=True,
            verbose_enabled=verbose_logging,
        )
        deadline = (
            time.monotonic() + timeout_seconds
            if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0
            else None
        )
        run_until_stopped = getattr(runner, "run_until_stopped", None)
        if not callable(run_until_stopped):
            raise RuntimeError("root runner loop expected run_until_stopped for readiness wait")
        last_status: str | None = None
        loops = 0
        while True:
            loops += 1
            self._drain_worker_replies(
                ingress=ingress,
                worker_ids=expected_worker_ids,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            status = self._readiness_status(
                events=state.events(),
                expected_worker_ids=expected_worker_ids,
            )
            if status != last_status and verbose_logging:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane readiness status changed",
                    fields={
                        "event": "control_plane.runtime.readiness_status",
                        "status": status,
                        "loops": loops,
                        "expected_workers": expected_worker_ids,
                    },
                    verbose_only=True,
                    verbose_enabled=True,
                )
                last_status = status
            if verbose_logging and loops % 25 == 0:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane readiness heartbeat",
                    fields={
                        "event": "control_plane.runtime.readiness_heartbeat",
                        "loops": loops,
                        "status": status,
                        "worker_status": self._latest_worker_statuses(
                            events=state.events(),
                            expected_worker_ids=expected_worker_ids,
                        ),
                    },
                    verbose_only=True,
                    verbose_enabled=True,
                )
            if status == "ready":
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane leaf readiness reached",
                    fields={
                        "event": "control_plane.runtime.readiness_reached",
                        "loops": loops,
                    },
                    verbose_only=True,
                    verbose_enabled=verbose_logging,
                )
                return
            if status == "rejected":
                rejected_worker, rejected_error = self._latest_rejected_worker_ack(
                    events=state.events(),
                    expected_worker_ids=expected_worker_ids,
                )
                details = []
                if isinstance(rejected_worker, str) and rejected_worker:
                    details.append(f"worker_id={rejected_worker}")
                if isinstance(rejected_error, str) and rejected_error:
                    details.append(f"error={rejected_error}")
                suffix = f" ({', '.join(details)})" if details else ""
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="error",
                    message="control-plane leaf readiness rejected",
                    fields={
                        "event": "control_plane.runtime.readiness_rejected",
                        "worker_id": rejected_worker,
                        "error": rejected_error,
                    },
                )
                raise RuntimeError(f"control-plane readiness failed: leaf config rejected{suffix}")
            run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            if deadline is not None and time.monotonic() >= deadline:
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="warning" if not fail_on_timeout else "error",
                    message="control-plane readiness timeout",
                    fields={
                        "event": "control_plane.runtime.readiness_timeout",
                        "expected_workers": expected_worker_ids,
                        "fail_on_timeout": fail_on_timeout,
                    },
                )
                if fail_on_timeout:
                    raise RuntimeError("control-plane readiness timeout waiting for leaf config ack")
                return

    def _resolve_readiness_wait_settings(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> tuple[bool, float | None, bool]:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return (False, None, False)
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return (False, None, False)
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return (False, None, False)
        readiness = platform.get("readiness")
        if not isinstance(readiness, dict):
            return (False, None, False)
        enabled = readiness.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            return (False, None, False)
        start_on_all_ready = readiness.get("start_work_on_all_groups_ready", True)
        if not isinstance(start_on_all_ready, bool) or not start_on_all_ready:
            return (False, None, False)
        fail_on_timeout_raw = readiness.get("fail_on_timeout", False)
        fail_on_timeout = bool(fail_on_timeout_raw) if isinstance(fail_on_timeout_raw, bool) else False
        timeout = readiness.get("readiness_timeout_seconds")
        if isinstance(timeout, (int, float)) and float(timeout) > 0:
            return (True, float(timeout), fail_on_timeout)
        return (True, 30.0, fail_on_timeout)

    def _resolve_root_verbose_logging(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> bool:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return False
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return False
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return False
        debug = platform.get("debug", {})
        if not isinstance(debug, dict):
            return False
        value = debug.get("root_verbose_logging", False)
        return bool(value) if isinstance(value, bool) else False

    def _resolve_post_start_settle_settings(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> tuple[bool, float, float]:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return (True, 30.0, 5.0)
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return (True, 30.0, 5.0)
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return (True, 30.0, 5.0)
        runner_loop = platform.get("runner_loop", {})
        if not isinstance(runner_loop, dict):
            return (True, 30.0, 5.0)
        enabled_raw = runner_loop.get("post_start_settle_enabled", True)
        enabled = bool(enabled_raw) if isinstance(enabled_raw, bool) else True
        max_wait_raw = runner_loop.get("post_start_settle_max_wait_seconds", 30.0)
        quiet_raw = runner_loop.get("post_start_settle_quiet_window_seconds", 5.0)
        max_wait = float(max_wait_raw) if isinstance(max_wait_raw, (int, float)) else 30.0
        quiet = float(quiet_raw) if isinstance(quiet_raw, (int, float)) else 1.0
        return (enabled, max(0.1, max_wait), max(0.05, quiet))

    def _settle_after_start_work(
        self,
        *,
        runner: object,
        scenario_scope: ScenarioScope,
        run_id: str,
        scenario_id: str,
        inputs: list[object] | tuple[object, ...] | object,
        source_start_targets: tuple[str, ...],
        next_replay_index: int,
        replay_root_boundary_outputs: Callable[..., int],
        poll_timeout_seconds: float,
        idle_timeout_seconds: float | None,
        verbose_logging: bool,
    ) -> int:
        from stream_kernel.execution.transport.handoff.replay import (
            root_boundary_handoff_has_inflight,
        )

        enabled, max_wait_seconds, quiet_window_seconds = self._resolve_post_start_settle_settings(inputs)
        if not enabled:
            return next_replay_index
        require_tombstones = self._runtime_has_tombstone_enabled_sources(
            inputs=inputs,
            source_start_targets=source_start_targets,
        )
        required_tombstone_groups = (
            self._expected_business_groups_for_tombstone(inputs)
            if require_tombstones
            else set()
        )
        observed_tombstones_last_count = -1
        shutdown_ready_last = False
        run_until_stopped = getattr(runner, "run_until_stopped", None)
        if not callable(run_until_stopped):
            return next_replay_index
        deadline = time.monotonic() + max_wait_seconds
        last_activity = time.monotonic()
        loops = 0
        settle_idle_timeout = max(0.001, float(poll_timeout_seconds))
        while time.monotonic() < deadline:
            loops += 1
            before_index = next_replay_index
            pending_external = getattr(runner, "external_deliveries", None)
            pending_external_count = (
                len(pending_external) if isinstance(pending_external, list) else 0
            )
            next_replay_index = replay_root_boundary_outputs(
                runner=runner,
                scenario_scope=scenario_scope,
                run_id=run_id,
                scenario_id=scenario_id,
                start_index=next_replay_index,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            has_external = isinstance(getattr(runner, "external_deliveries", None), list) and bool(
                getattr(runner, "external_deliveries", None)
            )
            has_inflight = root_boundary_handoff_has_inflight(scenario_scope)
            observed_tombstones = self._observed_tombstone_completed_groups(scenario_scope)
            observed_count = len(observed_tombstones)
            tombstones_progressed = observed_count != observed_tombstones_last_count
            observed_tombstones_last_count = observed_count
            shutdown_ready = self._is_shutdown_ready(scenario_scope)
            shutdown_ready_progressed = shutdown_ready != shutdown_ready_last
            shutdown_ready_last = shutdown_ready
            progressed = (
                next_replay_index != before_index
                or has_external
                or has_inflight
                or tombstones_progressed
                or shutdown_ready_progressed
            )
            missing_tombstones = sorted(required_tombstone_groups - observed_tombstones)
            if verbose_logging and (loops == 1 or loops % 25 == 0):
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane post-start settle heartbeat",
                    fields={
                        "event": "control_plane.runtime.post_start_settle_heartbeat",
                        "loops": loops,
                        "next_replay_index_before": before_index,
                        "next_replay_index_after": next_replay_index,
                        "pending_external_count": pending_external_count,
                        "has_external": has_external,
                        "has_inflight": has_inflight,
                        "require_tombstones": require_tombstones,
                        "required_tombstones": sorted(required_tombstone_groups),
                        "observed_tombstones": sorted(observed_tombstones),
                        "missing_tombstones": missing_tombstones,
                        "shutdown_ready": shutdown_ready,
                        "progressed": progressed,
                    },
                    verbose_only=True,
                    verbose_enabled=True,
                )
            if progressed:
                last_activity = time.monotonic()
                run_until_stopped(
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=settle_idle_timeout,
                )
                continue
            self._pump_root_startup_replies(
                scenario_scope=scenario_scope,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=settle_idle_timeout,
            )
            if (
                require_tombstones
                and required_tombstone_groups
                and not shutdown_ready
            ):
                continue
            if time.monotonic() - last_activity >= quiet_window_seconds:
                break
        self._emit_root_log(
            scenario_scope=scenario_scope,
            level="debug",
            message="control-plane post-start settle finished",
            fields={
                "event": "control_plane.runtime.post_start_settle_finished",
                "loops": loops,
                "next_replay_index": next_replay_index,
                "max_wait_seconds": max_wait_seconds,
                "quiet_window_seconds": quiet_window_seconds,
                "require_tombstones": require_tombstones,
                "required_tombstones": sorted(required_tombstone_groups),
                "observed_tombstones": sorted(self._observed_tombstone_completed_groups(scenario_scope)),
                "shutdown_ready": self._is_shutdown_ready(scenario_scope),
            },
            verbose_only=True,
            verbose_enabled=verbose_logging,
        )
        return next_replay_index

    def _expected_business_groups_for_tombstone(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> set[str]:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return set()
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return set()
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return set()
        groups = platform.get("process_groups", [])
        if not isinstance(groups, list):
            return set()
        expected: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                continue
            name = group.get("name")
            if not isinstance(name, str) or not name:
                continue
            if name.startswith("system."):
                continue
            expected.add(name)
        return expected

    def _runtime_has_tombstone_enabled_sources(
        self,
        *,
        inputs: list[object] | tuple[object, ...] | object,
        source_start_targets: tuple[str, ...],
    ) -> bool:
        if not source_start_targets:
            return False
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return False
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return False
        platform = runtime.get("platform", {})
        if isinstance(platform, dict):
            source_ingress = platform.get("source_ingress", {})
            if isinstance(source_ingress, dict) and source_ingress.get("emit_tombstone") is True:
                return True
        adapters = self._collect_runtime_adapter_configs(runtime)
        if not adapters:
            return False
        for target in source_start_targets:
            if not isinstance(target, str) or not target.startswith("source:"):
                continue
            role = target.split(":", 1)[1]
            cfg = adapters.get(role)
            if not isinstance(cfg, dict):
                continue
            if cfg.get("emit_tombstone") is True:
                return True
        return False

    @staticmethod
    def _collect_runtime_adapter_configs(runtime: dict[str, object]) -> dict[str, dict[str, object]]:
        candidates: list[object] = []
        candidates.append(runtime.get("adapters"))
        candidates.append(runtime.get("__adapters"))
        resolved: dict[str, dict[str, object]] = {}
        for raw in candidates:
            if not isinstance(raw, dict):
                continue
            for role, cfg in raw.items():
                if not isinstance(role, str) or not role:
                    continue
                if not isinstance(cfg, dict):
                    continue
                resolved[role] = dict(cfg)
        return resolved

    def _configure_shutdown_readiness_expected_groups(
        self,
        *,
        scenario_scope: ScenarioScope,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> None:
        readiness = self._resolve_shutdown_readiness_service(scenario_scope)
        if readiness is None:
            return
        expected = tuple(sorted(self._expected_business_groups_for_tombstone(inputs)))
        if not expected:
            return
        configure = getattr(readiness, "configure_expected_groups", None)
        if not callable(configure):
            return
        try:
            configure(expected)
        except Exception:
            return

    def _is_shutdown_ready(self, scenario_scope: ScenarioScope) -> bool:
        state = self._resolve_control_plane_state_service(scenario_scope)
        if state is None:
            return False
        try:
            events = state.events()
        except Exception:
            return False
        for event in reversed(events):
            if isinstance(event, ControlPlaneShutdownReadyEvent):
                return True
            if isinstance(event, dict) and event.get("kind") == "control_plane.shutdown.all_ready":
                return True
        return False

    def _observed_tombstone_completed_groups(self, scenario_scope: ScenarioScope) -> set[str]:
        state = self._resolve_control_plane_state_service(scenario_scope)
        if state is None:
            return set()
        observed: set[str] = set()
        try:
            events = state.events()
        except Exception:
            return observed
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "leaf_tombstone_completed":
                continue
            group_name = event.get("target_group")
            if isinstance(group_name, str) and group_name:
                observed.add(group_name)
        return observed

    def _emit_root_log(
        self,
        *,
        scenario_scope: ScenarioScope,
        level: str,
        message: str,
        fields: dict[str, object] | None = None,
        verbose_only: bool = False,
        verbose_enabled: bool = False,
    ) -> None:
        if verbose_only and not verbose_enabled:
            return
        dispatch = self._resolve_root_console_dispatch_service(scenario_scope)
        payload_fields = {"process_name": "supervisor"}
        if isinstance(fields, dict):
            payload_fields.update(fields)
        if dispatch is not None:
            try:
                dispatch.publish(
                    LogMessage(
                        level=level,
                        message=message,
                        fields=payload_fields,
                    )
                )
                return
            except Exception:
                pass
        self._emit_root_log_stdout_fallback(
            level=level,
            message=message,
            fields=payload_fields,
        )

    @staticmethod
    def _emit_root_log_stdout_fallback(
        *,
        level: str,
        message: str,
        fields: dict[str, object],
    ) -> None:
        try:
            kv_pairs = [f"{key}={fields[key]}" for key in sorted(fields)]
            suffix = f" | {' '.join(kv_pairs)}" if kv_pairs else ""
            print(
                f"[supervisor-fallback]: [{level.upper()}]: {message}{suffix}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            return

    @staticmethod
    def _resolve_shutdown_readiness_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.platform.services.runtime.control_plane_shutdown_readiness import (
            ControlPlaneShutdownReadinessService,
        )

        try:
            resolved = resolve("service", ControlPlaneShutdownReadinessService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneShutdownReadinessService):
            return resolved
        if callable(getattr(resolved, "configure_expected_groups", None)):
            return resolved
        return None

    @staticmethod
    def _resolve_control_plane_state_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.platform.services.runtime.control_plane_state import (
            ControlPlaneStateService,
        )

        try:
            resolved = resolve("service", ControlPlaneStateService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneStateService):
            return resolved
        if callable(getattr(resolved, "events", None)):
            return resolved
        return None

    @staticmethod
    def _resolve_expected_worker_ids_for_readiness(events: list[object]) -> list[str]:
        worker_ids: list[str] = []
        seen: set[str] = set()
        for event in events:
            worker_id = None
            if isinstance(event, dict) and event.get("kind") == "control_plane.lifecycle.worker_spawned":
                worker_id = event.get("worker_id")
            if isinstance(worker_id, str) and worker_id and worker_id not in seen:
                seen.add(worker_id)
                worker_ids.append(worker_id)
        if worker_ids:
            return worker_ids
        for event in events:
            if isinstance(event, dict) and event.get("kind") == "control_plane.lifecycle.spawn_requested":
                group_name = event.get("group_name")
                workers = event.get("workers", 1)
                if not isinstance(group_name, str) or not group_name:
                    continue
                if not isinstance(workers, int) or workers <= 0:
                    continue
                for index in range(workers):
                    worker_id = f"{group_name}#{index + 1}"
                    if worker_id in seen:
                        continue
                    seen.add(worker_id)
                    worker_ids.append(worker_id)
        return worker_ids

    @staticmethod
    def _readiness_status(
        *,
        events: list[object],
        expected_worker_ids: list[str],
    ) -> str:
        latest_status: dict[str, str] = {}
        for event in events:
            if not isinstance(event, ControlPlaneLeafConfigAckEvent):
                continue
            worker_id = event.worker_id
            status = event.status
            if worker_id not in expected_worker_ids:
                continue
            latest_status[worker_id] = status
        if any(latest_status.get(worker_id) == "rejected" for worker_id in expected_worker_ids):
            return "rejected"
        if all(latest_status.get(worker_id) == "applied" for worker_id in expected_worker_ids):
            return "ready"
        return "pending"

    @staticmethod
    def _latest_rejected_worker_ack(
        *,
        events: list[object],
        expected_worker_ids: list[str],
    ) -> tuple[str | None, str | None]:
        for event in reversed(events):
            if not isinstance(event, ControlPlaneLeafConfigAckEvent):
                continue
            worker_id = event.worker_id
            status = event.status
            error = event.error
            if status != "rejected":
                continue
            if worker_id not in expected_worker_ids:
                continue
            return (worker_id, error)
        return (None, None)

    @staticmethod
    def _latest_worker_statuses(
        *,
        events: list[object],
        expected_worker_ids: list[str],
    ) -> dict[str, str]:
        statuses: dict[str, str] = {}
        expected = set(expected_worker_ids)
        for event in events:
            if not isinstance(event, ControlPlaneLeafConfigAckEvent):
                continue
            worker_id = event.worker_id
            status = event.status
            if worker_id not in expected:
                continue
            statuses[worker_id] = status
        return statuses

    @staticmethod
    def _drain_worker_replies(
        *,
        ingress: object,
        worker_ids: list[str],
        poll_timeout_seconds: float,
    ) -> None:
        for index, worker_id in enumerate(worker_ids):
            timeout = max(0.0, float(poll_timeout_seconds)) if index == 0 else 0.0
            try:
                ingress.drain_worker_replies(
                    worker_id=worker_id,
                    timeout_seconds=timeout,
                    max_items=64,
                )
            except Exception:
                continue

    @staticmethod
    def _resolve_root_reply_ingress_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
            ControlPlaneRootReplyIngressService,
        )

        try:
            resolved = resolve("service", ControlPlaneRootReplyIngressService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneRootReplyIngressService):
            return resolved
        if callable(getattr(resolved, "drain_worker_replies", None)):
            return resolved
        return None

    @staticmethod
    def _resolve_root_console_dispatch_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
            RootConsoleLogDispatchService,
        )

        try:
            resolved = resolve("service", RootConsoleLogDispatchService)
        except Exception:
            return None
        if isinstance(resolved, RootConsoleLogDispatchService):
            return resolved
        if callable(getattr(resolved, "publish", None)):
            return resolved
        return None

    @staticmethod
    def _resolve_spawned_worker_ids(scenario_scope: ScenarioScope) -> list[str]:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return []
        from stream_kernel.platform.services.runtime.control_plane_state import (
            ControlPlaneStateService,
        )

        try:
            resolved = resolve("service", ControlPlaneStateService)
        except Exception:
            return []
        events_getter = getattr(resolved, "events", None)
        if not callable(events_getter):
            return []
        try:
            events = list(events_getter())
        except Exception:
            return []
        worker_ids: list[str] = []
        seen: set[str] = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "control_plane.lifecycle.worker_spawned":
                continue
            worker_id = event.get("worker_id")
            if not isinstance(worker_id, str) or not worker_id:
                continue
            if worker_id in seen:
                continue
            seen.add(worker_id)
            worker_ids.append(worker_id)
        return worker_ids

    @staticmethod
    def _require_positive_milliseconds(value: object, path: str) -> float:
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number when provided")
        milliseconds = float(value)
        if milliseconds <= 0:
            raise ValueError(f"{path} must be > 0")
        return milliseconds

    def _optional_positive_milliseconds_to_seconds(self, value: object, path: str) -> float | None:
        if value is None:
            return None
        return self._require_positive_milliseconds(value, path) / 1000.0


__all__ = ["RootRunnerLoopOrchestrationService"]
