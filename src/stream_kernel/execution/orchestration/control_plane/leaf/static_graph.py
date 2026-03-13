from __future__ import annotations

from stream_kernel.execution.orchestration.scheduler_system_nodes import (
    SCHEDULER_COMMAND_NODE_NAME,
    SCHEDULER_TIMER_NODE_NAME,
    SCHEDULER_TICK_NODE_NAME,
    PlatformSchedulerCommandNode,
    PlatformSchedulerTimerNode,
    PlatformSchedulerTickNode,
)
from stream_kernel.kernel.scenario import StepSpec

LEAF_STATIC_SYSTEM_NODE_NAMES: tuple[str, ...] = (
    "system.cp.bootstrap_dispatch",
    "system.cp.leaf_bootstrap",
    "system.cp.initialization_dispatch",
    "system.cp.initialization_plan",
    "system.cp.node_initialize",
    "system.cp.ready_for_work",
    SCHEDULER_COMMAND_NODE_NAME,
    SCHEDULER_TIMER_NODE_NAME,
    SCHEDULER_TICK_NODE_NAME,
    "system.cp.consumer_registry_bindings_bootstrap",
    "system.cp.consumer_registry_bindings_apply",
    "system.cp.deferred_message_hold",
    "system.cp.deferred_message_replay",
    "system.cp.leaf_reply_dispatch",
    "system.cp.consumer_registry_discovery_apply",
    "system.cp.consumer_registry_remove",
    "system.cp.leaf_discovery",
    "system.cp.leaf_snapshot_apply",
    "system.cp.leaf_apply_config",
    "system.cp.leaf_start_work",
    "system.cp.leaf_boundary_execute",
    "system.cp.leaf_tombstone_finalize",
    "system.cp.leaf_stop",
)


def build_leaf_static_system_steps(
    *,
    bootstrap_dispatch: object,
    leaf: object,
    initialization_dispatch: object,
    initialization_plan: object,
    node_initialize: object,
    ready_for_work: object,
    consumer_registry_bindings_bootstrap: object,
    consumer_registry_bindings_apply: object,
    deferred_message_hold: object,
    deferred_message_replay: object,
    leaf_reply_dispatch: object,
    consumer_registry_discovery_apply: object,
    consumer_registry_remove: object,
    leaf_discovery: object,
    leaf_snapshot: object,
    leaf_apply: object,
    leaf_start_work: object,
    leaf_boundary: object,
    leaf_tombstone_finalize: object,
    leaf_stop: object,
) -> list[StepSpec]:
    return [
        StepSpec(name="system.cp.bootstrap_dispatch", step=bootstrap_dispatch),
        StepSpec(name="system.cp.leaf_bootstrap", step=leaf),
        StepSpec(name="system.cp.initialization_dispatch", step=initialization_dispatch),
        StepSpec(name="system.cp.initialization_plan", step=initialization_plan),
        StepSpec(name="system.cp.node_initialize", step=node_initialize),
        StepSpec(name="system.cp.ready_for_work", step=ready_for_work),
        StepSpec(name=SCHEDULER_COMMAND_NODE_NAME, step=PlatformSchedulerCommandNode()),
        StepSpec(name=SCHEDULER_TIMER_NODE_NAME, step=PlatformSchedulerTimerNode()),
        StepSpec(name=SCHEDULER_TICK_NODE_NAME, step=PlatformSchedulerTickNode()),
        StepSpec(name="system.cp.consumer_registry_bindings_bootstrap", step=consumer_registry_bindings_bootstrap),
        StepSpec(name="system.cp.consumer_registry_bindings_apply", step=consumer_registry_bindings_apply),
        StepSpec(name="system.cp.deferred_message_hold", step=deferred_message_hold),
        StepSpec(name="system.cp.deferred_message_replay", step=deferred_message_replay),
        StepSpec(name="system.cp.leaf_reply_dispatch", step=leaf_reply_dispatch),
        StepSpec(name="system.cp.consumer_registry_discovery_apply", step=consumer_registry_discovery_apply),
        StepSpec(name="system.cp.consumer_registry_remove", step=consumer_registry_remove),
        StepSpec(name="system.cp.leaf_discovery", step=leaf_discovery),
        StepSpec(name="system.cp.leaf_snapshot_apply", step=leaf_snapshot),
        StepSpec(name="system.cp.leaf_apply_config", step=leaf_apply),
        StepSpec(name="system.cp.leaf_start_work", step=leaf_start_work),
        StepSpec(name="system.cp.leaf_boundary_execute", step=leaf_boundary),
        StepSpec(name="system.cp.leaf_tombstone_finalize", step=leaf_tombstone_finalize),
        StepSpec(name="system.cp.leaf_stop", step=leaf_stop),
    ]


__all__ = [
    "LEAF_STATIC_SYSTEM_NODE_NAMES",
    "build_leaf_static_system_steps",
]
