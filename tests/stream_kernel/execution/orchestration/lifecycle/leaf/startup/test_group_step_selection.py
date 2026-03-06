from __future__ import annotations

from stream_kernel.execution.orchestration.lifecycle.leaf.startup.group_step_selection import (
    select_group_planning_steps,
)


def test_group_step_selection_keeps_transport_handoff_and_start_work_rails() -> None:
    scenario_steps = {
        "source:source": object(),
        "parse_load_attempt": object(),
        "compute_time_keys": object(),
        "system.obs.trace_dispatch": object(),
        "system.transport.handoff.observability_dispatch": object(),
        "system.cp.leaf_start_work": object(),
    }
    runtime = {
        "platform": {
            "process_groups": [
                {
                    "name": "execution.ingress",
                    "nodes": ["source:source", "parse_load_attempt"],
                }
            ]
        }
    }

    selected = select_group_planning_steps(
        scenario_steps=scenario_steps,
        runtime=runtime,
        process_group="execution.ingress",
    )

    assert "source:source" in selected
    assert "parse_load_attempt" in selected
    assert "compute_time_keys" not in selected
    assert "system.obs.trace_dispatch" in selected
    assert "system.transport.handoff.observability_dispatch" in selected
    assert "system.cp.leaf_start_work" in selected
