from __future__ import annotations

from collections.abc import Callable

from stream_kernel.application_context.service import service
from stream_kernel.routing.router import RoutingResult


class BootstrapSupervisor:
    # Bootstrap supervisor contract for process-group orchestration in process_supervisor mode.
    def load_bootstrap_channel(self, channel: object) -> None:
        # Optional Step-C hook for one-shot bootstrap key bundle distribution.
        _ = channel
        return None

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        # Optional Step-D hook for metadata-only child runtime bootstrap contract.
        _ = bundle
        return None

    def start_groups(self, group_names: list[str]) -> None:
        raise NotImplementedError("BootstrapSupervisor.start_groups must be implemented")

    def wait_ready(self, timeout_seconds: int) -> bool:
        raise NotImplementedError("BootstrapSupervisor.wait_ready must be implemented")

    def execute_boundary(
        self,
        *,
        run: Callable[[], None],
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        # Optional Step-D/E hook: execute workload through process boundary and return structured result.
        _ = (run, run_id, scenario_id, inputs)
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        raise NotImplementedError("BootstrapSupervisor.stop_groups must be implemented")

    def force_terminate_groups(self, group_names: list[str]) -> None:
        # Optional Step-E hook used when graceful stop timeout occurs.
        _ = group_names
        return None

    def emit_stop_event(self, *, group_name: str, mode: str) -> None:
        # Optional Step-E hook for lifecycle stop telemetry.
        _ = (group_name, mode)
        return None

    def emit_handoff_failure(self, *, group_name: str, category: str) -> None:
        # Optional Step-E hook for sanitized remote-handoff diagnostics.
        _ = (group_name, category)
        return None


@service(name="bootstrap_supervisor_local")
class LocalBootstrapSupervisor(BootstrapSupervisor):
    # In-process bootstrap baseline; starts requested groups in-place and reports immediate readiness.
    def __init__(self) -> None:
        self._started = False
        self._child_bundle: object | None = None

    def start_groups(self, group_names: list[str]) -> None:
        _ = group_names
        self._started = True

    def wait_ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        return self._started

    def load_child_bootstrap_bundle(self, bundle: object) -> None:
        self._child_bundle = bundle

    def execute_boundary(
        self,
        *,
        run: Callable[[], None],
        run_id: str,
        scenario_id: str,
        inputs: list[object],
    ) -> RoutingResult:
        # Local baseline keeps legacy behavior but can execute boundary batch via child bootstrap loop.
        _ = (run_id, scenario_id)
        if self._child_bundle is not None and inputs:
            try:
                from stream_kernel.execution.orchestration.child_bootstrap import (
                    ChildBootstrapBundle,
                    execute_child_boundary_loop_from_bundle,
                )

                if isinstance(self._child_bundle, ChildBootstrapBundle):
                    outputs = execute_child_boundary_loop_from_bundle(
                        bundle=self._child_bundle,
                        inputs=list(inputs),
                    )
                    return RoutingResult(
                        local_deliveries=[],
                        boundary_deliveries=[],
                        terminal_outputs=list(outputs),
                    )
            except Exception:
                # Fallback to in-process callback path if child boundary bootstrap is not ready.
                pass
        run()
        return RoutingResult(local_deliveries=[], boundary_deliveries=[], terminal_outputs=[])

    def stop_groups(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = graceful_timeout_seconds
        _ = drain_inflight
        self._started = False

    def force_terminate_groups(self, group_names: list[str]) -> None:
        _ = group_names
        self._started = False
