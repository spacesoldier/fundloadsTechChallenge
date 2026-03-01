from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_bundle import (
    build_child_bootstrap_bundle,
)
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import (
    build_bootstrap_key_bundle,
)
from stream_kernel.execution.orchestration.observability_system_nodes import (
    _SYSTEM_NODE_KIND_TO_EVENT,
    _build_system_node_name,
    _resolve_system_nodes_config,
)
from stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service import (
    ControlPlaneLifecycleOrchestrationService,
)
from stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service import (
    ControlPlaneRootBoundaryHandoffService,
)
from stream_kernel.execution.orchestration.control_plane.root.leaf_command_service import (
    ControlPlaneRootLeafCommandService,
)
from stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service import (
    ControlPlaneRootReplyIngressService,
)
from stream_kernel.execution.transport.handoff.ipc_route_table_service import (
    ExecutionIpcRouteTableService,
)
from stream_kernel.platform.services.runtime import ProcessGroupRouterService


@runtime_checkable
class ControlPlaneRootRuntimeBootstrapService(Protocol):
    def prepare_root_runtime(
        self,
        *,
        runtime: dict[str, object],
        config: dict[str, object],
        adapters: dict[str, object],
        run_id: str,
        scenario_id: str,
        discovery_modules: list[str],
    ) -> None:
        raise NotImplementedError


@service(name="control_plane_root_runtime_bootstrap_service")
@dataclass(slots=True)
class DefaultControlPlaneRootRuntimeBootstrapService(ControlPlaneRootRuntimeBootstrapService):
    lifecycle: ControlPlaneLifecycleOrchestrationService = inject.service(
        ControlPlaneLifecycleOrchestrationService
    )
    process_group_router: ProcessGroupRouterService = inject.service(ProcessGroupRouterService)
    root_boundary_handoff: object = inject.service(ControlPlaneRootBoundaryHandoffService)
    root_leaf_commands: object = inject.service(ControlPlaneRootLeafCommandService)
    root_reply_ingress: object = inject.service(ControlPlaneRootReplyIngressService)
    route_table: object | None = inject.service(ExecutionIpcRouteTableService)

    def prepare_root_runtime(
        self,
        *,
        runtime: dict[str, object],
        config: dict[str, object],
        adapters: dict[str, object],
        run_id: str,
        scenario_id: str,
        discovery_modules: list[str],
    ) -> None:
        runtime_map = dict(runtime or {}) if isinstance(runtime, dict) else {}
        config_map = dict(config or {}) if isinstance(config, dict) else {}
        adapters_map = dict(adapters or {}) if isinstance(adapters, dict) else {}
        key_bundle = build_bootstrap_key_bundle(runtime_map)
        child_bundle = build_child_bootstrap_bundle(
            scenario_id=scenario_id,
            run_id=run_id,
            process_group=None,
            discovery_modules=list(discovery_modules),
            runtime=runtime_map,
            config=config_map,
            adapters=adapters_map,
            key_bundle=key_bundle,
        )
        self._lifecycle().configure_spawn_context(
            child_bundle=child_bundle,
            boundary_control_poll_seconds=_boundary_control_poll_seconds(runtime_map),
            pipe_codec_mode=_pipe_codec_mode(runtime_map, adapters_map),
        )
        self._lifecycle().configure_group_runner_profiles(_group_runner_profiles(runtime_map))
        configured_groups = self._configure_process_group_router(runtime_map)
        self._preload_route_table_snapshot(configured_groups)
        self._configure_root_boundary_handoff(runtime_map)
        self._configure_root_leaf_command_poll(runtime_map)
        self._configure_root_reply_ingress(runtime_map)

    def _lifecycle(self) -> ControlPlaneLifecycleOrchestrationService:
        candidate = self.lifecycle
        if isinstance(candidate, ControlPlaneLifecycleOrchestrationService):
            return candidate
        if callable(getattr(candidate, "configure_spawn_context", None)):
            return candidate  # type: ignore[return-value]
        raise ValueError("ControlPlaneLifecycleOrchestrationService binding is required")

    def _configure_process_group_router(self, runtime: dict[str, object]) -> list[dict[str, object]]:
        router = self._process_group_router_optional()
        if router is None:
            return []
        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            return []
        groups = platform.get("process_groups", [])
        configured_groups: list[dict[str, object]] = []
        if isinstance(groups, list):
            configured_groups = _router_process_groups(groups=groups, runtime=runtime)
            try:
                router.configure_process_groups(configured_groups)
            except Exception:
                configured_groups = []
        routing_cache = platform.get("routing_cache", {})
        if isinstance(routing_cache, dict):
            try:
                router.configure_routing_cache(dict(routing_cache))
            except Exception:
                pass
        return configured_groups

    def _process_group_router_optional(self) -> ProcessGroupRouterService | None:
        candidate = self.process_group_router
        if isinstance(candidate, ProcessGroupRouterService):
            return candidate
        if callable(getattr(candidate, "configure_process_groups", None)) and callable(
            getattr(candidate, "configure_routing_cache", None)
        ):
            return candidate  # type: ignore[return-value]
        return None

    def _configure_root_boundary_handoff(self, runtime: dict[str, object]) -> None:
        candidate = self.root_boundary_handoff
        configure = getattr(candidate, "configure_dispatch", None)
        if not callable(configure):
            return
        boundary_dispatch = _boundary_dispatch_settings(runtime)
        timeout_seconds = boundary_dispatch.get("timeout_seconds")
        stream_batch_max_items = boundary_dispatch.get("stream_batch_max_items")
        observability_batch_max_items = boundary_dispatch.get("batch_max_items")
        inflight_idle_timeout_seconds = boundary_dispatch.get("inflight_idle_timeout_seconds")
        if inflight_idle_timeout_seconds is None and isinstance(timeout_seconds, (int, float)):
            inflight_idle_timeout_seconds = float(timeout_seconds)
        configure(
            timeout_seconds=timeout_seconds if isinstance(timeout_seconds, (int, float)) else None,
            stream_batch_max_items=(
                int(stream_batch_max_items)
                if isinstance(stream_batch_max_items, int) and stream_batch_max_items > 0
                else None
            ),
            observability_batch_max_items=(
                int(observability_batch_max_items)
                if isinstance(observability_batch_max_items, int) and observability_batch_max_items > 0
                else None
            ),
            inflight_idle_timeout_seconds=(
                float(inflight_idle_timeout_seconds)
                if isinstance(inflight_idle_timeout_seconds, (int, float))
                and float(inflight_idle_timeout_seconds) > 0
                else None
            ),
        )

    def _configure_root_leaf_command_poll(self, runtime: dict[str, object]) -> None:
        candidate = self.root_leaf_commands
        configure = getattr(candidate, "configure_poll_interval_seconds", None)
        if not callable(configure):
            return
        configure(_boundary_control_poll_seconds(runtime))

    def _configure_root_reply_ingress(self, runtime: dict[str, object]) -> None:
        candidate = self.root_reply_ingress
        configure = getattr(candidate, "configure_startup_protocol_revision", None)
        if callable(configure):
            configure(_startup_protocol_revision(runtime))
        elif hasattr(candidate, "startup_protocol_revision"):
            try:
                setattr(candidate, "startup_protocol_revision", _startup_protocol_revision(runtime))
            except Exception:
                pass
        configure_fallback = getattr(candidate, "configure_discovery_request_fallback", None)
        fallback_enabled = _discovery_request_fallback_enabled(runtime)
        if callable(configure_fallback):
            configure_fallback(fallback_enabled)
        elif hasattr(candidate, "discovery_request_fallback_enabled"):
            try:
                setattr(candidate, "discovery_request_fallback_enabled", fallback_enabled)
            except Exception:
                pass

    def _preload_route_table_snapshot(self, groups: list[dict[str, object]]) -> None:
        route_table = self._route_table_optional()
        if route_table is None:
            return
        snapshot = _route_table_snapshot_from_groups(groups)
        if not snapshot:
            return
        preload = getattr(route_table, "preload_snapshot", None)
        if callable(preload):
            try:
                preload(routes=dict(snapshot))
                return
            except Exception:
                return
        upsert = getattr(route_table, "upsert_route", None)
        if not callable(upsert):
            return
        for target, target_id in snapshot.items():
            try:
                upsert(target=target, target_id=target_id)
            except Exception:
                continue

    def _route_table_optional(self) -> ExecutionIpcRouteTableService | None:
        candidate = self.route_table
        if isinstance(candidate, ExecutionIpcRouteTableService):
            return candidate
        if callable(getattr(candidate, "upsert_route", None)):
            return candidate  # type: ignore[return-value]
        return None


def _group_runner_profiles(runtime: dict[str, object]) -> dict[str, str]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return {}
    profiles: dict[str, str] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        profile = item.get("runner_profile")
        if isinstance(name, str) and name and isinstance(profile, str) and profile:
            profiles[name] = profile
    return profiles


def _boundary_control_poll_seconds(runtime: dict[str, object]) -> float:
    boundary_dispatch = _boundary_dispatch_settings(runtime)
    control_poll_ms = boundary_dispatch.get("control_poll_ms")
    if isinstance(control_poll_ms, (int, float)) and float(control_poll_ms) > 0:
        return float(control_poll_ms) / 1000.0
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return 0.001
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return 0.001
    explicit = execution_ipc.get("boundary_control_poll_seconds")
    if isinstance(explicit, (int, float)) and float(explicit) > 0:
        return float(explicit)
    return 0.001


def _boundary_dispatch_settings(runtime: dict[str, object]) -> dict[str, object]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    boundary_dispatch = platform.get("boundary_dispatch", {})
    if not isinstance(boundary_dispatch, dict):
        return {}
    return dict(boundary_dispatch)


def _startup_protocol_revision(runtime: dict[str, object]) -> int:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return 3
    control_plane = platform.get("control_plane", {})
    if not isinstance(control_plane, dict):
        return 3
    revision = control_plane.get("startup_protocol_revision")
    if isinstance(revision, int) and revision >= 1:
        return int(revision)
    return 3


def _discovery_request_fallback_enabled(runtime: dict[str, object]) -> bool:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return True
    control_plane = platform.get("control_plane", {})
    if not isinstance(control_plane, dict):
        return True
    raw = control_plane.get("discovery_request_fallback")
    if isinstance(raw, bool):
        return raw
    return True


def _pipe_codec_mode(runtime: dict[str, object], adapters: dict[str, object]) -> str:
    platform = runtime.get("platform", {})
    if isinstance(platform, dict):
        execution_ipc = platform.get("execution_ipc", {})
        if isinstance(execution_ipc, dict):
            codec = execution_ipc.get("codec")
            if isinstance(codec, str) and codec:
                return codec
    for cfg in adapters.values():
        if not isinstance(cfg, dict):
            continue
        settings = cfg.get("settings")
        if not isinstance(settings, dict):
            continue
        codec = settings.get("codec")
        if isinstance(codec, str) and codec:
            return codec
    return "pickle"


__all__ = [
    "ControlPlaneRootRuntimeBootstrapService",
    "DefaultControlPlaneRootRuntimeBootstrapService",
]


def _router_process_groups(*, groups: list[object], runtime: dict[str, object]) -> list[dict[str, object]]:
    configured: list[dict[str, object]] = [
        dict(item) for item in groups if isinstance(item, dict)
    ]
    observability_node_map = _observability_group_system_nodes(runtime)
    if not observability_node_map:
        return configured
    enriched: list[dict[str, object]] = []
    for item in configured:
        name = item.get("name")
        if not isinstance(name, str) or name not in observability_node_map:
            enriched.append(item)
            continue
        merged = dict(item)
        nodes = merged.get("nodes")
        existing_nodes = [node for node in nodes if isinstance(node, str) and node] if isinstance(nodes, list) else []
        merged["nodes"] = _merge_unique_nodes(existing_nodes, observability_node_map[name])
        enriched.append(merged)
    return enriched


def _observability_group_system_nodes(runtime: dict[str, object]) -> dict[str, list[str]]:
    if not _is_observability_root_transport_only(runtime):
        return {}
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return {}
    service_process = observability.get("service_process", {})
    if not isinstance(service_process, dict):
        # accept legacy alias if present before validation normalization
        service_process = observability.get("service_worker", {})
        if not isinstance(service_process, dict):
            return {}
    group_name = service_process.get("group_name")
    if not isinstance(group_name, str) or not group_name:
        group_name = "system.observability"
    nodes_cfg = _resolve_system_nodes_config(observability)
    if not isinstance(nodes_cfg, list) or not nodes_cfg:
        return {}
    node_names: list[str] = []
    kind_counts: dict[str, int] = {}
    for cfg in nodes_cfg:
        if not isinstance(cfg, dict):
            continue
        kind = cfg.get("kind")
        if not isinstance(kind, str) or kind not in _SYSTEM_NODE_KIND_TO_EVENT:
            continue
        enabled = cfg.get("enabled", True)
        if enabled is False:
            continue
        qualifier = cfg.get("qualifier") if isinstance(cfg.get("qualifier"), str) else None
        suffix_index = kind_counts.get(kind, 0)
        kind_counts[kind] = suffix_index + 1
        node_names.append(_build_system_node_name(kind=kind, qualifier=qualifier, index=suffix_index))
    if not node_names:
        return {}
    return {group_name: _merge_unique_nodes([], node_names)}


def _merge_unique_nodes(existing: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for name in [*existing, *extra]:
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        merged.append(name)
    return merged


def _route_table_snapshot_from_groups(groups: list[dict[str, object]]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        nodes = group.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        worker_id = f"{group_name}#1"
        for node_name in nodes:
            if not isinstance(node_name, str) or not node_name:
                continue
            snapshot.setdefault(node_name, worker_id)
    return snapshot


def _is_observability_root_transport_only(runtime: dict[str, object]) -> bool:
    role = runtime.get("__process_role")
    if isinstance(role, str) and role in {"worker", "observability_worker"}:
        return False
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return False
    bootstrap = platform.get("bootstrap", {})
    if not isinstance(bootstrap, dict) or bootstrap.get("mode") != "process_supervisor":
        return False
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return False
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict):
        service_process = observability.get("service_worker")
    if not isinstance(service_process, dict):
        return False
    return service_process.get("enabled") is True
