from __future__ import annotations

from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    InMemoryControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    InMemoryControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ExecutionGroupConfigRecord,
)
from stream_kernel.platform.services.runtime.control_plane_launch_plan import (
    DefaultControlPlaneLaunchPlanService,
)


def test_launch_plan_service_builds_groups_from_config_store_without_discovery_pruning() -> None:
    config_store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    config_store.append(
        ExecutionGroupConfigRecord(
            source="runtime",
            section="execution_group",
            record_id="execution_group:0:execution.alpha",
            payload={"name": "execution.alpha", "workers": 2, "nodes": ["node.keep", "node.drop"]},
        )
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    discovery.append_item(
        ControlPlaneDiscoveryEntityRecord(
            entity_kind="node",
            entity_id="pkg.mod:NodeKeep",
            source_scope="platform",
            module="pkg.mod",
            qualname="NodeKeep",
            meta={"name": "node.keep"},
        )
    )
    service = DefaultControlPlaneLaunchPlanService(config_store=config_store, discovery=discovery)

    plan = service.build_plan(runtime={"platform": {"process_groups": []}})

    assert plan is not None
    assert len(plan.groups) == 1
    assert plan.groups[0].group_name == "execution.alpha"
    assert plan.groups[0].workers == 2
    assert plan.groups[0].nodes == ("node.keep", "node.drop")


def test_launch_plan_service_falls_back_to_runtime_process_groups_when_store_is_empty() -> None:
    config_store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    service = DefaultControlPlaneLaunchPlanService(config_store=config_store, discovery=discovery)

    plan = service.build_plan(
        runtime={
            "platform": {
                "process_groups": [
                    {"name": "execution.runtime", "workers": 1, "nodes": ["node.r1", "node.r2"]}
                ]
            }
        }
    )

    assert plan is not None
    assert len(plan.groups) == 1
    assert plan.groups[0].group_name == "execution.runtime"
    assert plan.groups[0].nodes == ("node.r1", "node.r2")


def test_launch_plan_service_keeps_transport_alias_nodes_from_runtime_groups() -> None:
    config_store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    service = DefaultControlPlaneLaunchPlanService(config_store=config_store, discovery=discovery)

    plan = service.build_plan(
        runtime={
            "platform": {
                "process_groups": [
                    {
                        "name": "execution.pipe",
                        "workers": 1,
                        "nodes": ["source:source", "ingress_line_bridge", "sink:sink"],
                    }
                ]
            }
        }
    )

    assert plan is not None
    assert len(plan.groups) == 1
    assert plan.groups[0].group_name == "execution.pipe"
    assert plan.groups[0].nodes == ("source:source", "ingress_line_bridge", "sink:sink")


def test_launch_plan_service_appends_observability_service_worker_group_when_missing() -> None:
    config_store = InMemoryControlPlaneStartupConfigStore(store=InMemoryKvStore())
    config_store.append(
        ExecutionGroupConfigRecord(
            source="runtime",
            section="execution_group",
            record_id="execution_group:0:execution.runtime",
            payload={"name": "execution.runtime", "workers": 1, "nodes": ["node.r1"]},
        )
    )
    discovery = InMemoryControlPlaneDiscoveryService(store=InMemoryKvStore())
    service = DefaultControlPlaneLaunchPlanService(config_store=config_store, discovery=discovery)

    plan = service.build_plan(
        runtime={
            "platform": {
                "bootstrap": {"mode": "process_supervisor"},
                "process_groups": [],
            },
            "observability": {
                "service_worker": {
                    "enabled": True,
                    "group_name": "system.observability",
                },
                "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "logging": {"exporters": [{"kind": "jsonl", "enabled": True}]},
                "monitoring": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            },
        }
    )

    assert plan is not None
    assert len(plan.groups) == 2
    obs = next(group for group in plan.groups if group.group_name == "system.observability")
    assert obs.workers == 1
    assert obs.nodes == (
        "system.obs.trace_dispatch",
        "system.obs.log_dispatch",
        "system.obs.monitor_dispatch",
        "system.obs.monitoring_metrics_dispatch",
    )
