from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.lifecycle import (
    execute_with_runtime_lifecycle,
    runtime_lifecycle_policy,
)
from stream_kernel.platform.services.observability import NoOpObservabilityService, ObservabilityService
from stream_kernel.platform.services.runtime.lifecycle import RuntimeLifecycleManager


@dataclass
class _Lifecycle(RuntimeLifecycleManager):
    started: int = 0
    stopped: int = 0
    ready_checks: int = 0
    shutdown_policy_calls: list[dict[str, object]] = field(default_factory=list)

    def start(self) -> None:
        self.started += 1

    def ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        self.ready_checks += 1
        return True

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = (graceful_timeout_seconds, drain_inflight)
        self.stopped += 1

    def configure_shutdown_policy(
        self,
        *,
        observability_group_name: str | None = None,
        observability_stop_command_timeout_seconds: float | None = None,
        stop_command_timeout_seconds: float | None = None,
        fallback_graceful_timeout_seconds: float | None = None,
    ) -> None:
        self.shutdown_policy_calls.append(
            {
                "observability_group_name": observability_group_name,
                "observability_stop_command_timeout_seconds": observability_stop_command_timeout_seconds,
                "stop_command_timeout_seconds": stop_command_timeout_seconds,
                "fallback_graceful_timeout_seconds": fallback_graceful_timeout_seconds,
            }
        )


@dataclass
class _Obs(NoOpObservabilityService):
    lifecycle_events: list[dict[str, object]] = field(default_factory=list)

    def on_runtime_lifecycle_event(
        self,
        *,
        event: str,
        process_group: str | None,
        details: dict[str, object] | None,
    ) -> None:
        self.lifecycle_events.append(
            {
                "event": event,
                "process_group": process_group,
                "details": dict(details or {}),
            }
        )


def test_obs_k_c_06_runtime_lifecycle_emits_observability_events() -> None:
    lifecycle = _Lifecycle()
    obs = _Obs()
    registry = InjectionRegistry()
    registry.register_factory("service", RuntimeLifecycleManager, lambda _s=lifecycle: _s)
    registry.register_factory("service", ObservabilityService, lambda _o=obs: _o)
    scope = registry.instantiate_for_scenario("s1")

    execute_with_runtime_lifecycle(
        runtime={"platform": {"lifecycle": {"ready_timeout_seconds": 5, "graceful_timeout_seconds": 3}}},
        scenario_scope=scope,
        run=lambda: None,
    )

    assert lifecycle.started == 1
    assert lifecycle.ready_checks == 1
    assert lifecycle.stopped == 1
    names = [item["event"] for item in obs.lifecycle_events]
    assert names == [
        "runtime_lifecycle_starting",
        "runtime_lifecycle_ready",
        "runtime_run_started",
        "runtime_run_completed",
        "runtime_lifecycle_stopped",
    ]


def test_runtime_lifecycle_policy_uses_readiness_timeout_when_lifecycle_timeout_missing() -> None:
    policy = runtime_lifecycle_policy(
        {
            "platform": {
                "readiness": {
                    "readiness_timeout_seconds": 30,
                }
            }
        }
    )
    assert policy.ready_timeout_seconds == 30


def test_runtime_lifecycle_policy_prefers_lifecycle_timeout_over_readiness_timeout() -> None:
    policy = runtime_lifecycle_policy(
        {
            "platform": {
                "readiness": {
                    "readiness_timeout_seconds": 30,
                },
                "lifecycle": {
                    "ready_timeout_seconds": 7,
                },
            }
        }
    )
    assert policy.ready_timeout_seconds == 7


def test_execute_with_runtime_lifecycle_configures_observability_shutdown_policy() -> None:
    lifecycle = _Lifecycle()
    registry = InjectionRegistry()
    registry.register_factory("service", RuntimeLifecycleManager, lambda _s=lifecycle: _s)
    registry.register_factory("service", ObservabilityService, lambda: _Obs())
    scope = registry.instantiate_for_scenario("s2")

    execute_with_runtime_lifecycle(
        runtime={
            "platform": {"lifecycle": {"ready_timeout_seconds": 2, "graceful_timeout_seconds": 3}},
            "observability": {
                "service_worker": {
                    "group_name": "system.observability",
                    "stop_command_timeout_seconds": 12,
                }
            },
        },
        scenario_scope=scope,
        run=lambda: None,
    )

    assert lifecycle.shutdown_policy_calls
    call = lifecycle.shutdown_policy_calls[-1]
    assert call["observability_group_name"] == "system.observability"
    assert call["observability_stop_command_timeout_seconds"] == 12


def test_execute_with_runtime_lifecycle_does_not_use_observability_drain_timeout_as_stop_command_timeout() -> None:
    lifecycle = _Lifecycle()
    registry = InjectionRegistry()
    registry.register_factory("service", RuntimeLifecycleManager, lambda _s=lifecycle: _s)
    registry.register_factory("service", ObservabilityService, lambda: _Obs())
    scope = registry.instantiate_for_scenario("s3")

    execute_with_runtime_lifecycle(
        runtime={
            "platform": {"lifecycle": {"ready_timeout_seconds": 2, "graceful_timeout_seconds": 3}},
            "observability": {
                "service_worker": {
                    "group_name": "system.observability",
                    "drain_timeout_seconds": 120,
                }
            },
        },
        scenario_scope=scope,
        run=lambda: None,
    )

    assert lifecycle.shutdown_policy_calls
    call = lifecycle.shutdown_policy_calls[-1]
    assert call["observability_group_name"] == "system.observability"
    assert call["observability_stop_command_timeout_seconds"] == 5.0


def test_execute_with_runtime_lifecycle_passes_stop_and_fallback_shutdown_timeouts() -> None:
    lifecycle = _Lifecycle()
    registry = InjectionRegistry()
    registry.register_factory("service", RuntimeLifecycleManager, lambda _s=lifecycle: _s)
    registry.register_factory("service", ObservabilityService, lambda: _Obs())
    scope = registry.instantiate_for_scenario("s4")

    execute_with_runtime_lifecycle(
        runtime={
            "platform": {
                "lifecycle": {
                    "ready_timeout_seconds": 2,
                    "graceful_timeout_seconds": 3,
                    "stop_command_timeout_seconds": 1.5,
                    "fallback_graceful_timeout_seconds": 2.0,
                }
            },
            "observability": {
                "service_worker": {
                    "group_name": "system.observability",
                    "stop_command_timeout_seconds": 7,
                }
            },
        },
        scenario_scope=scope,
        run=lambda: None,
    )

    assert lifecycle.shutdown_policy_calls
    call = lifecycle.shutdown_policy_calls[-1]
    assert call["observability_group_name"] == "system.observability"
    assert call["observability_stop_command_timeout_seconds"] == 7
    assert call["stop_command_timeout_seconds"] == 1.5
    assert call["fallback_graceful_timeout_seconds"] == 2.0


def test_execute_with_runtime_lifecycle_keeps_observability_default_stop_timeout_when_base_timeout_is_set() -> None:
    lifecycle = _Lifecycle()
    registry = InjectionRegistry()
    registry.register_factory("service", RuntimeLifecycleManager, lambda _s=lifecycle: _s)
    registry.register_factory("service", ObservabilityService, lambda: _Obs())
    scope = registry.instantiate_for_scenario("s5")

    execute_with_runtime_lifecycle(
        runtime={
            "platform": {
                "lifecycle": {
                    "ready_timeout_seconds": 2,
                    "graceful_timeout_seconds": 3,
                    "stop_command_timeout_seconds": 1.0,
                }
            },
            "observability": {
                "service_worker": {
                    "group_name": "system.observability",
                }
            },
        },
        scenario_scope=scope,
        run=lambda: None,
    )

    assert lifecycle.shutdown_policy_calls
    call = lifecycle.shutdown_policy_calls[-1]
    assert call["observability_group_name"] == "system.observability"
    assert call["observability_stop_command_timeout_seconds"] == 5.0
    assert call["stop_command_timeout_seconds"] == 1.0
