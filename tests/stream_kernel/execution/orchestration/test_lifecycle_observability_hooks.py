from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.lifecycle_orchestration import (
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

    def start(self) -> None:
        self.started += 1

    def ready(self, timeout_seconds: int) -> bool:
        _ = timeout_seconds
        self.ready_checks += 1
        return True

    def stop(self, *, graceful_timeout_seconds: int, drain_inflight: bool) -> None:
        _ = (graceful_timeout_seconds, drain_inflight)
        self.stopped += 1


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
