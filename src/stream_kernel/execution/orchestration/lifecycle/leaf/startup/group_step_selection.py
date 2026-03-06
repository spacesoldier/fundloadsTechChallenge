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
    # Framework-internal rails must stay available in every worker group.
    # - system.obs.*: observability dispatch sinks
    # - system.transport.handoff.*: observability transport relay/handoff nodes
    # - system.cp.leaf_start_work: control-plane start signal -> local source bootstrap
    for node_name in scenario_steps:
        if node_name.startswith("system.obs."):
            selected_nodes.add(node_name)
            continue
        if node_name.startswith("system.transport.handoff."):
            selected_nodes.add(node_name)
            continue
        if node_name == "system.cp.leaf_start_work":
            selected_nodes.add(node_name)
    return {
        name: step
        for name, step in scenario_steps.items()
        if name in selected_nodes
    }


__all__ = ["select_group_planning_steps"]
