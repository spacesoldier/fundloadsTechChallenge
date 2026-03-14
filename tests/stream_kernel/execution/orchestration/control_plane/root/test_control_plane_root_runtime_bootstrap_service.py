from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class _LifecycleService:
    spawn_context_calls: list[dict[str, object]] = field(default_factory=list)
    runner_profile_calls: list[dict[str, str]] = field(default_factory=list)

    def on_spawn_requested(self, event: object) -> None:
        _ = event

    def resolve_registered_endpoint(self, target_id: str) -> object | None:
        _ = target_id
        return None

    def configure_spawn_context(self, **kwargs: object) -> None:
        self.spawn_context_calls.append(dict(kwargs))

    def configure_group_runner_profiles(self, profiles: dict[str, str] | None) -> None:
        self.runner_profile_calls.append(dict(profiles or {}))


@dataclass(slots=True)
class _ProcessGroupRouter:
    process_groups_calls: list[list[dict[str, object]]] = field(default_factory=list)
    routing_cache_calls: list[dict[str, object]] = field(default_factory=list)

    def configure_process_groups(self, groups: list[dict[str, object]]) -> None:
        self.process_groups_calls.append(list(groups))

    def configure_routing_cache(self, settings: dict[str, object]) -> None:
        self.routing_cache_calls.append(dict(settings))


@dataclass(slots=True)
class _RootBoundaryHandoff:
    configure_calls: list[dict[str, object]] = field(default_factory=list)

    def configure_dispatch(
        self,
        *,
        timeout_seconds: float | None = None,
        stream_batch_max_items: int | None = None,
        observability_batch_max_items: int | None = None,
        inflight_idle_timeout_seconds: float | None = None,
    ) -> None:
        self.configure_calls.append(
            {
                "timeout_seconds": timeout_seconds,
                "stream_batch_max_items": stream_batch_max_items,
                "observability_batch_max_items": observability_batch_max_items,
                "inflight_idle_timeout_seconds": inflight_idle_timeout_seconds,
            }
        )


@dataclass(slots=True)
class _RootLeafIngress:
    protocol_revision_calls: list[int] = field(default_factory=list)

    def configure_startup_protocol_revision(self, revision: int) -> None:
        self.protocol_revision_calls.append(int(revision))


@dataclass(slots=True)
class _RouteTable:
    upsert_calls: list[tuple[str, str]] = field(default_factory=list)

    def upsert_route(self, *, target: str, target_id: str) -> None:
        self.upsert_calls.append((target, target_id))


def test_root_runtime_bootstrap_service_configures_lifecycle_spawn_context_and_group_profiles() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    service = DefaultControlPlaneRootRuntimeBootstrapService(lifecycle=lifecycle, process_group_router=router)
    runtime = {
        "platform": {
            "execution_ipc": {"auth": {"secret_mode": "static", "secret": "abc"}},
            "routing_cache": {"enabled": True, "negative_cache": False, "max_entries": 123},
            "process_groups": [
                {"name": "execution.alpha", "runner_profile": "async", "nodes": ["node.a"]},
                {"name": "execution.beta", "runner_profile": "sync", "nodes": ["node.b"]},
            ],
        }
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={"execution_ipc_pipe": {"settings": {"codec": "pickle"}}},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load", "stream_kernel.platform"],
    )

    assert lifecycle.spawn_context_calls
    call = lifecycle.spawn_context_calls[-1]
    assert call["pipe_codec_mode"] == "pickle"
    assert float(call["boundary_control_poll_seconds"]) > 0
    bundle = call["child_bundle"]
    assert getattr(bundle, "scenario_id") == "scenario-1"
    assert getattr(bundle, "run_id") == "run-1"
    assert list(getattr(bundle, "discovery_modules")) == ["fund_load", "stream_kernel.platform"]
    assert isinstance(getattr(bundle, "runtime"), dict)
    assert lifecycle.runner_profile_calls == [
        {"execution.alpha": "async", "execution.beta": "sync"}
    ]
    assert router.process_groups_calls == [[
        {"name": "execution.alpha", "runner_profile": "async", "nodes": ["node.a"]},
        {"name": "execution.beta", "runner_profile": "sync", "nodes": ["node.b"]},
    ]]
    assert router.routing_cache_calls == [{"enabled": True, "negative_cache": False, "max_entries": 123}]


def test_root_runtime_bootstrap_service_enriches_observability_group_with_system_nodes_for_router() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    service = DefaultControlPlaneRootRuntimeBootstrapService(lifecycle=lifecycle, process_group_router=router)
    runtime = {
        "observability": {
            "service_process": {"enabled": True, "group_name": "system.observability"},
            "tracing": {
                "exporters": [
                    {"kind": "jsonl", "enabled": True},
                ]
            },
            "logging": {
                "exporters": [
                    {"kind": "stdout_plain", "enabled": True},
                ]
            },
            "monitoring": {
                "exporters": [
                    {"kind": "jsonl", "enabled": True},
                ]
            },
            "worker_queue_telemetry": {"enabled": True},
        },
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [
                {"name": "execution.alpha", "nodes": ["node.a"]},
                {"name": "system.observability"},
            ],
        },
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load"],
    )

    assert router.process_groups_calls, "process groups must be configured"
    configured_groups = router.process_groups_calls[-1]
    observability_group = next(
        item for item in configured_groups if isinstance(item, dict) and item.get("name") == "system.observability"
    )
    nodes = observability_group.get("nodes")
    assert isinstance(nodes, list)
    assert "system.obs.trace_dispatch" in nodes
    assert "system.obs.log_dispatch" in nodes
    assert "system.obs.monitor_dispatch" in nodes
    assert "system.obs.worker_queue_dispatch" in nodes


def test_root_runtime_bootstrap_service_adds_missing_observability_group_for_router_and_route_table() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    route_table = _RouteTable()
    service = DefaultControlPlaneRootRuntimeBootstrapService(
        lifecycle=lifecycle,
        process_group_router=router,
        route_table=route_table,
    )
    runtime = {
        "observability": {
            "service_worker": {"enabled": True, "group_name": "system.observability"},
            "tracing": {"exporters": [{"kind": "jsonl", "enabled": True}]},
            "logging": {"exporters": [{"kind": "stdout_plain", "enabled": True}]},
            "monitoring": {"exporters": [{"kind": "jsonl", "enabled": True}]},
        },
        "platform": {
            "bootstrap": {"mode": "process_supervisor"},
            "process_groups": [
                {"name": "execution.alpha", "nodes": ["node.a"]},
            ],
        },
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load"],
    )

    assert router.process_groups_calls, "process groups must be configured"
    configured_groups = router.process_groups_calls[-1]
    observability_group = next(
        item for item in configured_groups if isinstance(item, dict) and item.get("name") == "system.observability"
    )
    nodes = observability_group.get("nodes")
    assert isinstance(nodes, list)
    assert "system.obs.trace_dispatch" in nodes
    assert "system.obs.log_dispatch" in nodes
    assert "system.obs.monitor_dispatch" in nodes

    upserted_targets = {target for target, _target_id in route_table.upsert_calls}
    assert "system.obs.trace_dispatch" in upserted_targets
    assert "system.obs.log_dispatch" in upserted_targets
    assert "system.obs.monitor_dispatch" in upserted_targets


def test_root_runtime_bootstrap_service_configures_boundary_handoff_and_control_poll_from_runtime() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    handoff = _RootBoundaryHandoff()
    leaf_ingress = _RootLeafIngress()
    service = DefaultControlPlaneRootRuntimeBootstrapService(
        lifecycle=lifecycle,
        process_group_router=router,
        root_boundary_handoff=handoff,
        root_leaf_ingress=leaf_ingress,
    )
    runtime = {
        "platform": {
            "boundary_dispatch": {
                "stream_batch_max_items": 10,
                "timeout_seconds": 2.5,
                "control_poll_ms": 1.0,
            },
            "control_plane": {"startup_protocol_revision": 2},
            "process_groups": [{"name": "execution.alpha", "nodes": ["node.a"]}],
        }
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load"],
    )

    assert handoff.configure_calls == [
        {
            "timeout_seconds": 2.5,
            "stream_batch_max_items": 10,
            "observability_batch_max_items": None,
            "inflight_idle_timeout_seconds": None,
        }
    ]
    assert leaf_ingress.protocol_revision_calls == [2]


def test_root_runtime_bootstrap_service_defaults_to_snapshot_protocol_v3() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    leaf_ingress = _RootLeafIngress()
    service = DefaultControlPlaneRootRuntimeBootstrapService(
        lifecycle=lifecycle,
        process_group_router=router,
        root_leaf_ingress=leaf_ingress,
    )
    runtime = {
        "platform": {
            "process_groups": [{"name": "execution.alpha", "nodes": ["node.a"]}],
        }
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load"],
    )

    assert leaf_ingress.protocol_revision_calls == [3]


def test_root_runtime_bootstrap_service_preloads_route_table_snapshot_from_process_groups() -> None:
    from stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service import (
        DefaultControlPlaneRootRuntimeBootstrapService,
    )

    lifecycle = _LifecycleService()
    router = _ProcessGroupRouter()
    route_table = _RouteTable()
    service = DefaultControlPlaneRootRuntimeBootstrapService(
        lifecycle=lifecycle,
        process_group_router=router,
        route_table=route_table,
    )
    runtime = {
        "platform": {
            "process_groups": [
                {"name": "execution.alpha", "workers": 2, "nodes": ["node.a", "node.b"]},
                {"name": "execution.beta", "nodes": ["node.c"]},
            ],
        }
    }

    service.prepare_root_runtime(
        runtime=runtime,
        config={"runtime": runtime, "nodes": {}, "adapters": {}},
        adapters={},
        run_id="run-1",
        scenario_id="scenario-1",
        discovery_modules=["fund_load"],
    )

    assert route_table.upsert_calls == [
        ("node.a", "execution.alpha#1"),
        ("node.b", "execution.alpha#1"),
        ("node.c", "execution.beta#1"),
    ]
