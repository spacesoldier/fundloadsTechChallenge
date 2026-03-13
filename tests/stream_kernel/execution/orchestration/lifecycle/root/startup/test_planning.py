from __future__ import annotations

from stream_kernel.application_context.injection_registry import InjectionRegistry
from stream_kernel.execution.orchestration.lifecycle.root.startup.planning import (
    build_lifecycle_system_plan,
)


def test_lifecycle_planning_is_noop_for_process_supervisor_runtime() -> None:
    scope = InjectionRegistry().instantiate_for_scenario("s1")
    runtime = {
        "platform": {"bootstrap": {"mode": "process_supervisor"}, "process_groups": [{"name": "execution.alpha"}]},
    }

    plan = build_lifecycle_system_plan(runtime=runtime, scenario_scope=scope)

    assert plan.system_steps == []
    assert plan.system_consumers == {}
    assert plan.system_node_names == set()


def test_lifecycle_planning_is_noop_for_leaf_runtime() -> None:
    scope = InjectionRegistry().instantiate_for_scenario("s1")
    runtime = {
        "__process_role": "worker",
        "platform": {"bootstrap": {"mode": "process_supervisor"}, "process_groups": [{"name": "execution.alpha"}]},
    }

    plan = build_lifecycle_system_plan(runtime=runtime, scenario_scope=scope)

    assert plan.system_steps == []
    assert plan.system_consumers == {}
    assert plan.system_node_names == set()
