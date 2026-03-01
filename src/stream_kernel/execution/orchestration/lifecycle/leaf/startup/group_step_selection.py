from __future__ import annotations

from typing import Any


def select_group_planning_steps(
    *,
    scenario_steps: dict[str, Any],
    runtime: dict[str, object],
    process_group: str | None,
) -> dict[str, object]:
    # Runner profile diagnostics should reflect current worker group, not full topology.
    if not isinstance(process_group, str) or not process_group:
        return dict(scenario_steps)
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return dict(scenario_steps)
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return dict(scenario_steps)

    selected_nodes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if name != process_group:
            continue
        nodes = group.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        for node_name in nodes:
            if isinstance(node_name, str) and node_name:
                selected_nodes.add(node_name)
        break
    if not selected_nodes:
        return dict(scenario_steps)
    # System observability nodes are framework-internal rails and must be available
    # in each worker group so trace/log/metric dispatch does not leak as remote handoff.
    for node_name in scenario_steps:
        if node_name.startswith("system.obs."):
            selected_nodes.add(node_name)
    return {
        name: step
        for name, step in scenario_steps.items()
        if name in selected_nodes
    }


__all__ = ["select_group_planning_steps"]
