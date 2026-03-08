from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneGroupSpec,
    ControlPlaneLaunchPlan,
)


@runtime_checkable
class ControlPlaneLaunchPlanService(Protocol):
    def build_plan(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        raise NotImplementedError("ControlPlaneLaunchPlanService.build_plan must be implemented")


@service(name="control_plane_launch_plan_service")
@dataclass(slots=True)
class DefaultControlPlaneLaunchPlanService(ControlPlaneLaunchPlanService):
    config_store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)

    def build_plan(self, *, runtime: dict[str, object]) -> ControlPlaneLaunchPlan | None:
        discovered_node_names = _discovered_node_names(self.discovery)
        groups = _resolve_group_specs_from_store(
            self.config_store,
            discovered_node_names=discovered_node_names,
        )
        if not groups and _allow_runtime_process_group_fallback(runtime):
            groups = _resolve_group_specs_from_runtime(
                runtime,
                discovered_node_names=discovered_node_names,
            )
        groups = _append_observability_service_worker_group_specs(runtime=runtime, groups=groups)
        if not groups:
            return None
        return ControlPlaneLaunchPlan(groups=tuple(groups))


def _allow_runtime_process_group_fallback(runtime: dict[str, object]) -> bool:
    # Startup source-of-truth in process_supervisor mode is config/discovery stores only.
    # Runtime-map fallback is permitted only for non-supervisor launch modes.
    if not isinstance(runtime, dict):
        return True
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return True
    bootstrap = platform.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        return True
    mode = bootstrap.get("mode", "local")
    return not (isinstance(mode, str) and mode == "process_supervisor")


def _resolve_group_specs_from_store(
    config_store: ControlPlaneStartupConfigStore | object,
    *,
    discovered_node_names: set[str] | None,
) -> list[ControlPlaneGroupSpec]:
    if not callable(getattr(config_store, "records", None)):
        return []
    try:
        records = list(config_store.records(section="execution_group"))
    except Exception:
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for record in records:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            continue
        group = _group_spec_from_mapping(payload, discovered_node_names=discovered_node_names)
        if group is not None:
            resolved.append(group)
    return resolved


def _resolve_group_specs_from_runtime(
    runtime: dict[str, object],
    *,
    discovered_node_names: set[str] | None,
) -> list[ControlPlaneGroupSpec]:
    if not isinstance(runtime, dict):
        return []
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return []
    resolved: list[ControlPlaneGroupSpec] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        mapped = _group_spec_from_mapping(group, discovered_node_names=discovered_node_names)
        if mapped is not None:
            resolved.append(mapped)
    return resolved


def _group_spec_from_mapping(
    payload: dict[str, object],
    *,
    discovered_node_names: set[str] | None,
) -> ControlPlaneGroupSpec | None:
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None
    workers = payload.get("workers", 1)
    if not isinstance(workers, int) or workers <= 0:
        return None
    raw_nodes = payload.get("nodes", [])
    nodes = (
        tuple(node_name for node_name in raw_nodes if isinstance(node_name, str) and node_name)
        if isinstance(raw_nodes, list)
        else ()
    )
    _ = discovered_node_names
    return ControlPlaneGroupSpec(group_name=name, workers=workers, nodes=nodes)


def _append_observability_service_worker_group_specs(
    *,
    runtime: dict[str, object],
    groups: list[ControlPlaneGroupSpec],
) -> list[ControlPlaneGroupSpec]:
    if not isinstance(runtime, dict):
        return groups
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return groups
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict):
        service_process = observability.get("service_worker")
    if not isinstance(service_process, dict):
        return groups
    if service_process.get("enabled") is not True:
        return groups

    group_name = service_process.get("group_name")
    if not isinstance(group_name, str) or not group_name:
        group_name = "system.observability"
    if any(spec.group_name == group_name for spec in groups):
        return groups

    workers = service_process.get("workers", 1)
    if not isinstance(workers, int) or workers <= 0:
        workers = 1
    nodes = _resolve_observability_service_worker_nodes(
        service_process=service_process,
        observability=observability,
    )
    return [*groups, ControlPlaneGroupSpec(group_name=group_name, workers=workers, nodes=nodes)]


def _resolve_observability_service_worker_nodes(
    *,
    service_process: dict[str, object],
    observability: dict[str, object],
) -> tuple[str, ...]:
    configured_nodes = service_process.get("nodes", [])
    if isinstance(configured_nodes, list):
        explicit = tuple(name for name in configured_nodes if isinstance(name, str) and name)
        if explicit:
            return explicit

    resolved: list[str] = []
    if _has_enabled_exporters(observability, "tracing"):
        resolved.append("system.obs.trace_dispatch")
    if _has_enabled_exporters(observability, "logging"):
        resolved.append("system.obs.log_dispatch")
    if _has_enabled_exporter_kind(observability, "logging", "redis_debug"):
        resolved.append("system.obs.debug_dispatch")
    if _has_enabled_exporters(observability, "telemetry"):
        resolved.append("system.obs.metric_dispatch")
    if _has_enabled_exporters(observability, "monitoring"):
        resolved.append("system.obs.monitor_dispatch")
        resolved.append("system.obs.monitoring_metrics_dispatch")
    worker_queue_telemetry = observability.get("worker_queue_telemetry", {})
    if isinstance(worker_queue_telemetry, dict) and worker_queue_telemetry.get("enabled") is True:
        resolved.append("system.obs.worker_queue_dispatch")
    return tuple(resolved)


def _has_enabled_exporters(observability: dict[str, object], section: str) -> bool:
    channel = observability.get(section)
    if not isinstance(channel, dict):
        return False
    exporters = channel.get("exporters", [])
    if not isinstance(exporters, list):
        return False
    return any(isinstance(exporter, dict) and exporter.get("enabled", True) is not False for exporter in exporters)


def _has_enabled_exporter_kind(
    observability: dict[str, object],
    section: str,
    kind: str,
) -> bool:
    channel = observability.get(section)
    if not isinstance(channel, dict):
        return False
    exporters = channel.get("exporters", [])
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(exporter, dict)
        and exporter.get("enabled", True) is not False
        and exporter.get("kind") == kind
        for exporter in exporters
    )


def _discovered_node_names(discovery: ControlPlaneDiscoveryService | object) -> set[str] | None:
    if callable(getattr(discovery, "entity_records", None)):
        try:
            items = list(discovery.entity_records(kind="node"))
        except Exception:
            items = []
        names = _extract_node_names(items)
        if names:
            return names
    if callable(getattr(discovery, "items", None)):
        try:
            items = list(discovery.items())
        except Exception:
            items = []
        names = _extract_node_names(items)
        if names:
            return names
    return None


def _extract_node_names(items: list[object]) -> set[str]:
    names: set[str] = set()
    for item in items:
        if not isinstance(item, ControlPlaneDiscoveryEntityRecord):
            continue
        if item.entity_kind != "node":
            continue
        node_name = item.meta.get("name")
        if isinstance(node_name, str) and node_name:
            names.add(node_name)
    return names


__all__ = [
    "ControlPlaneLaunchPlanService",
    "DefaultControlPlaneLaunchPlanService",
]
