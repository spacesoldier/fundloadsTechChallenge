from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneRootPulse,
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
        if root_control_plane_mode:
            setattr(runner, "allow_external_deliveries", True)
            setattr(runner, "external_deliveries", [])
            poll_timeout_seconds, idle_timeout_seconds = self.resolve_runner_loop_timeouts(inputs)
            startup_barrier_timeout_seconds = self.resolve_startup_barrier_timeout_seconds(inputs)
            startup_inputs, deferred_inputs = self.split_root_control_plane_inputs(inputs)
            next_replay_index = max((index for index, _payload in startup_inputs + deferred_inputs), default=0) + 1
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
            )
            self._wait_for_leaf_readiness_if_required(
                runner=runner,
                scenario_scope=scenario_scope,
                inputs=inputs,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
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
            if deferred_inputs:
                for index, payload in deferred_inputs:
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
                _ = replay_root_boundary_outputs(
                    runner=runner,
                    scenario_scope=scenario_scope,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    start_index=next_replay_index,
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
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
            drain_root_boundary_outputs(
                scenario_scope=scenario_scope,
                external_deliveries=list(external_deliveries),
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
        if root_control_plane_mode:
            setattr(runner, "allow_external_deliveries", True)
            setattr(runner, "external_deliveries", [])
            poll_timeout_seconds, idle_timeout_seconds = self.resolve_runner_loop_timeouts(inputs)
            startup_barrier_timeout_seconds = self.resolve_startup_barrier_timeout_seconds(inputs)
            startup_inputs, deferred_inputs = self.split_root_control_plane_inputs(inputs)
            next_replay_index = max((index for index, _payload in startup_inputs + deferred_inputs), default=0) + 1
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
            )
            self._wait_for_leaf_readiness_if_required(
                runner=runner,
                scenario_scope=scenario_scope,
                inputs=inputs,
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
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
            if deferred_inputs:
                for index, payload in deferred_inputs:
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
                _ = replay_root_boundary_outputs(
                    runner=runner,
                    scenario_scope=scenario_scope,
                    run_id=run_id,
                    scenario_id=scenario_id,
                    start_index=next_replay_index,
                    poll_timeout_seconds=poll_timeout_seconds,
                    idle_timeout_seconds=idle_timeout_seconds,
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
            drain_root_boundary_outputs(
                scenario_scope=scenario_scope,
                external_deliveries=list(external_deliveries),
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

    def _wait_for_startup_barrier_open(
        self,
        *,
        runner: object,
        scenario_scope: ScenarioScope,
        poll_timeout_seconds: float,
        idle_timeout_seconds: float | None,
        startup_barrier_timeout_seconds: float | None,
    ) -> None:
        barrier = self._resolve_startup_barrier_service(scenario_scope)
        if barrier is None:
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
            return
        deadline = (
            time.monotonic() + startup_barrier_timeout_seconds
            if startup_barrier_timeout_seconds is not None
            else None
        )
        run_until_stopped = getattr(runner, "run_until_stopped", None)
        if not callable(run_until_stopped):
            raise RuntimeError("root runner loop expected run_until_stopped for startup barrier wait")
        while not bool(is_open()):
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
                return
            if deadline is not None and time.monotonic() >= deadline:
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
    ) -> None:
        should_wait, timeout_seconds = self._resolve_readiness_wait_settings(inputs)
        if not should_wait:
            return
        ingress = self._resolve_root_reply_ingress_service(scenario_scope)
        state = self._resolve_control_plane_state_service(scenario_scope)
        if ingress is None or state is None:
            return
        expected_worker_ids = self._resolve_expected_worker_ids_for_readiness(state.events())
        if not expected_worker_ids:
            return
        deadline = (
            time.monotonic() + timeout_seconds
            if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0
            else None
        )
        run_until_stopped = getattr(runner, "run_until_stopped", None)
        if not callable(run_until_stopped):
            raise RuntimeError("root runner loop expected run_until_stopped for readiness wait")
        while True:
            self._drain_worker_replies(
                ingress=ingress,
                worker_ids=expected_worker_ids,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            status = self._readiness_status(
                events=state.events(),
                expected_worker_ids=expected_worker_ids,
            )
            if status == "ready":
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
                raise RuntimeError(f"control-plane readiness failed: leaf config rejected{suffix}")
            run_until_stopped(
                poll_timeout_seconds=poll_timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            if deadline is not None and time.monotonic() >= deadline:
                raise RuntimeError("control-plane readiness timeout waiting for leaf config ack")

    def _resolve_readiness_wait_settings(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> tuple[bool, float | None]:
        pulse = self._find_control_plane_root_pulse(inputs)
        if pulse is None:
            return (False, None)
        runtime = pulse.runtime
        if not isinstance(runtime, dict):
            return (False, None)
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return (False, None)
        readiness = platform.get("readiness", {})
        if not isinstance(readiness, dict):
            return (False, None)
        enabled = readiness.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            return (False, None)
        start_on_all_ready = readiness.get("start_work_on_all_groups_ready", False)
        if not isinstance(start_on_all_ready, bool) or not start_on_all_ready:
            return (False, None)
        timeout = readiness.get("readiness_timeout_seconds")
        if isinstance(timeout, (int, float)) and float(timeout) > 0:
            return (True, float(timeout))
        return (True, 30.0)

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
            worker_id: str | None = None
            status: str | None = None
            if isinstance(event, dict):
                worker_id_value = event.get("worker_id")
                status_value = event.get("status")
                if isinstance(worker_id_value, str) and worker_id_value:
                    worker_id = worker_id_value
                if isinstance(status_value, str) and status_value:
                    status = status_value
            else:
                worker_id_value = getattr(event, "worker_id", None)
                status_value = getattr(event, "status", None)
                if isinstance(worker_id_value, str) and worker_id_value:
                    worker_id = worker_id_value
                if isinstance(status_value, str) and status_value:
                    status = status_value
            if worker_id is None or status is None:
                continue
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
            worker_id: str | None = None
            status: str | None = None
            error: str | None = None
            if isinstance(event, dict):
                worker_id_value = event.get("worker_id")
                status_value = event.get("status")
                error_value = event.get("error")
                if isinstance(worker_id_value, str) and worker_id_value:
                    worker_id = worker_id_value
                if isinstance(status_value, str) and status_value:
                    status = status_value
                if isinstance(error_value, str) and error_value:
                    error = error_value
            else:
                worker_id_value = getattr(event, "worker_id", None)
                status_value = getattr(event, "status", None)
                error_value = getattr(event, "error", None)
                if isinstance(worker_id_value, str) and worker_id_value:
                    worker_id = worker_id_value
                if isinstance(status_value, str) and status_value:
                    status = status_value
                if isinstance(error_value, str) and error_value:
                    error = error_value
            if worker_id is None or status != "rejected":
                continue
            if worker_id not in expected_worker_ids:
                continue
            return (worker_id, error)
        return (None, None)

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
