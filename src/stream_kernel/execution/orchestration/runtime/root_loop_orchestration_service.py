from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.runtime.startup_mode import (
    is_root_process_supervisor_runtime,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.routing.envelope import Envelope


@service(name="root_runner_loop_orchestration_service")
@dataclass(slots=True)
class RootRunnerLoopOrchestrationService:
    default_poll_timeout_seconds: float = 0.01
    default_idle_timeout_seconds: float | None = 0.1

    def resolve_runner_loop_timeouts(
        self,
        runtime: dict[str, object] | None,
    ) -> tuple[float, float | None]:
        poll_timeout_seconds = self.default_poll_timeout_seconds
        idle_timeout_seconds: float | None = self.default_idle_timeout_seconds
        if runtime is None:
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

    def execute_sync(
        self,
        *,
        runner: object,
        inputs: list[object] | tuple[object, ...],
        run_id: str,
        scenario_id: str,
        scenario_scope: ScenarioScope,
        enqueue_runner_input: Callable[..., None],
    ) -> None:
        self.execute_async(
            runner=runner,
            inputs=inputs,
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_scope=scenario_scope,
            enqueue_runner_input=enqueue_runner_input,
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
    ) -> None:
        root_runtime = self._root_startup_runtime(inputs)
        root_control_plane_mode = root_runtime is not None
        root_verbose_logging = self._resolve_root_verbose_logging(root_runtime)
        if root_control_plane_mode:
            poll_timeout_seconds, idle_timeout_seconds = self.resolve_runner_loop_timeouts(root_runtime)
            root_runner_control = self._resolve_root_runner_control_service(scenario_scope)
            bind_stop = getattr(root_runner_control, "bind_runner_stop", None)
            clear_stop = getattr(root_runner_control, "clear_runner_stop", None)
            if callable(bind_stop):
                bind_stop(getattr(runner, "request_stop", None))
            self._emit_root_log(
                scenario_scope=scenario_scope,
                level="debug",
                message="control-plane root loop started",
                fields={
                    "event": "control_plane.runtime.root_loop_started",
                    "inputs": len(inputs),
                    "poll_timeout_seconds": poll_timeout_seconds,
                    "idle_timeout_seconds": idle_timeout_seconds,
                },
                verbose_only=True,
                verbose_enabled=root_verbose_logging,
            )
            try:
                for index, payload in enumerate(inputs, start=1):
                    enqueue_runner_input(
                        runner,
                        payload,
                        run_id=run_id,
                        scenario_id=scenario_id,
                        index=index,
                    )
                runner.run_until_stopped(
                    poll_timeout_seconds=poll_timeout_seconds,
                    # Root control-plane run is graph-driven and should stop only by
                    # explicit control-plane signal (system.cp.root_stop).
                    idle_timeout_seconds=(None if root_runner_control is not None else idle_timeout_seconds),
                )
            finally:
                if callable(clear_stop):
                    clear_stop()
                self._emit_root_log(
                    scenario_scope=scenario_scope,
                    level="debug",
                    message="control-plane root loop finished",
                    fields={
                        "event": "control_plane.runtime.root_loop_finished",
                        "next_runtime_index": len(inputs) + 1,
                    },
                    verbose_only=True,
                    verbose_enabled=root_verbose_logging,
                )
            return

        for index, payload in enumerate(inputs, start=1):
            enqueue_runner_input(
                runner,
                payload,
                run_id=run_id,
                scenario_id=scenario_id,
                index=index,
            )
            runner.run()

    def _root_startup_runtime(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> dict[str, object] | None:
        if not isinstance(inputs, list | tuple):
            return None
        for item in inputs:
            payload = self._payload(item)
            runtime = self._runtime_from_payload(payload)
            if is_root_process_supervisor_runtime(runtime):
                return runtime
        return None

    @staticmethod
    def _payload(item: object) -> object:
        if isinstance(item, Envelope):
            return item.payload
        return item

    def _resolve_source_start_targets_from_runtime(
        self,
        inputs: list[object] | tuple[object, ...] | object,
    ) -> list[str]:
        runtime = self._root_startup_runtime(inputs)
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
                if not _is_business_source_start_target(node_name):
                    continue
                if node_name in seen:
                    continue
                seen.add(node_name)
                targets.append(node_name)
        return targets

    def _resolve_root_verbose_logging(self, runtime: dict[str, object] | None) -> bool:
        if runtime is None:
            return False
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return False
        debug = platform.get("debug", {})
        if not isinstance(debug, dict):
            return False
        value = debug.get("root_verbose_logging", False)
        return bool(value) if isinstance(value, bool) else False

    @staticmethod
    def _runtime_from_payload(payload: object) -> dict[str, object] | None:
        runtime = getattr(payload, "runtime", None)
        if isinstance(runtime, dict):
            return runtime
        if isinstance(payload, dict):
            candidate = payload.get("runtime")
            if isinstance(candidate, dict):
                return candidate
            if "platform" in payload:
                return payload
        return None

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
    def _resolve_root_runner_control_service(scenario_scope: ScenarioScope) -> object | None:
        resolve = getattr(scenario_scope, "resolve", None)
        if not callable(resolve):
            return None
        from stream_kernel.execution.orchestration.control_plane.root.channel_services import (
            ControlPlaneRootRunnerControlService,
        )

        try:
            resolved = resolve("service", ControlPlaneRootRunnerControlService)
        except Exception:
            return None
        if isinstance(resolved, ControlPlaneRootRunnerControlService):
            return resolved
        if callable(getattr(resolved, "bind_runner_stop", None)):
            return resolved
        return None

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


def _is_business_source_start_target(target: str | None) -> bool:
    if not isinstance(target, str) or not target.startswith("source:"):
        return False
    lowered = target.lower()
    if lowered.startswith("source:system."):
        return False
    return True


__all__ = ["RootRunnerLoopOrchestrationService"]
