from __future__ import annotations

from dataclasses import dataclass, field

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service import (
    ControlPlaneLifecycleOrchestrationService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service import (
    RootConsoleLogDispatchService,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.log_factory_service import (
    RootLifecycleLogFactory,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.planning import (
    build_lifecycle_system_plan,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneSpawnRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneStateService,
)


@dataclass(slots=True)
class _LifecycleService(ControlPlaneLifecycleOrchestrationService):
    seen: list[object] = field(default_factory=list)

    def on_spawn_requested(self, event: object) -> None:
        self.seen.append(event)

    def resolve_registered_endpoint(self, target_id: str) -> object | None:
        _ = target_id
        return None

    def configure_spawn_context(self, **kwargs: object) -> None:
        _ = kwargs


@dataclass(slots=True)
class _LogFactory(RootLifecycleLogFactory):
    seen: list[object] = field(default_factory=list)

    def spawn_requested(self, event: object) -> LogMessage:
        self.seen.append(("spawn", event))
        return LogMessage(level="info", message="spawn")

    def group_startup_ready(self, *, event: object, worker_ids: tuple[str, ...]) -> LogMessage:
        self.seen.append(("ready", event, worker_ids))
        return LogMessage(level="info", message="ready")


@dataclass(slots=True)
class _ConsoleDispatch(RootConsoleLogDispatchService):
    seen: list[LogMessage] = field(default_factory=list)

    def publish(self, message: LogMessage) -> bool:
        self.seen.append(message)
        return True

    def drain(self, *, timeout_seconds: float = 1.0) -> bool:
        _ = timeout_seconds
        return True

    def stop(self, *, drain: bool = True, timeout_seconds: float = 1.0) -> None:
        _ = (drain, timeout_seconds)


@dataclass(slots=True)
class _StateService(ControlPlaneStateService):
    seen: list[object] = field(default_factory=list)

    def append_event(self, event: object) -> None:
        self.seen.append(event)

    def events(self) -> list[object]:
        return list(self.seen)


def _scope_with_lifecycle_service() -> tuple[InjectionRegistry, object, _StateService]:
    registry = InjectionRegistry()
    lifecycle = _LifecycleService()
    state = _StateService()
    log_factory = _LogFactory()
    console = _ConsoleDispatch()
    registry.register_factory(
        "service",
        ControlPlaneLifecycleOrchestrationService,
        lambda _svc=lifecycle: _svc,
    )
    registry.register_factory("service", ControlPlaneStateService, lambda _svc=state: _svc)
    registry.register_factory(
        "service",
        RootLifecycleLogFactory,
        lambda _svc=log_factory: _svc,
    )
    registry.register_factory(
        "service",
        RootConsoleLogDispatchService,
        lambda _svc=console: _svc,
    )
    return registry, lifecycle, state


def test_lifecycle_planning_builds_root_spawn_dispatch_node_and_injects_service() -> None:
    registry, lifecycle, state = _scope_with_lifecycle_service()
    scope = registry.instantiate_for_scenario("s1")
    runtime = {
        "strict": True,
        "platform": {"bootstrap": {"mode": "process_supervisor"}, "process_groups": [{"name": "execution.alpha"}]},
    }

    plan = build_lifecycle_system_plan(runtime=runtime, scenario_scope=scope)

    assert [spec.name for spec in plan.system_steps] == [
        "system.lifecycle.spawn_dispatch",
        "system.lifecycle.group_startup_wait",
        "system.lifecycle.log_dispatch",
    ]
    spawn_node = plan.system_steps[0].step
    wait_node = plan.system_steps[1].step
    assert getattr(spawn_node, "lifecycle") is lifecycle
    assert getattr(wait_node, "state") is state
    assert plan.system_consumers.get(ControlPlaneSpawnRequestedEvent) == [
        "system.lifecycle.spawn_dispatch",
        "system.lifecycle.group_startup_wait",
    ]
    assert plan.system_consumers.get(ControlPlaneLeafConfigAckEvent) == [
        "system.lifecycle.group_startup_wait",
    ]
    assert "system.lifecycle.log_dispatch" in plan.system_node_names


def test_lifecycle_planning_skips_nodes_for_leaf_process() -> None:
    registry, _, _ = _scope_with_lifecycle_service()
    scope = registry.instantiate_for_scenario("s1")
    runtime = {
        "__process_role": "worker",
        "strict": True,
        "platform": {"bootstrap": {"mode": "process_supervisor"}, "process_groups": [{"name": "execution.alpha"}]},
    }

    plan = build_lifecycle_system_plan(runtime=runtime, scenario_scope=scope)

    assert plan.system_steps == []
    assert plan.system_consumers == {}
    assert plan.system_node_names == set()


def test_lifecycle_planning_skips_nodes_for_inline_bootstrap_mode() -> None:
    registry, _, _ = _scope_with_lifecycle_service()
    scope = registry.instantiate_for_scenario("s1")
    runtime = {"strict": True, "platform": {"bootstrap": {"mode": "inline"}}}

    plan = build_lifecycle_system_plan(runtime=runtime, scenario_scope=scope)

    assert plan.system_steps == []
