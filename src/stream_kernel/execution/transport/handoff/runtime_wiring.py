from __future__ import annotations

from stream_kernel.application_context.injection_registry import (
    InjectionRegistry,
    InjectionRegistryError,
)
from stream_kernel.execution.orchestration.lifecycle.orchestration import runtime_bootstrap_mode
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore
from stream_kernel.execution.transport.ipc.flow_control import resolve_execution_ipc_flow_control
from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import PipeExecutionIpcTransportAdapter
from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_TRACE,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    ExecutionIpcEndpointRegistry,
    ExecutionIpcKvStreamPort,
    ExecutionIpcPort,
    ExecutionIpcReceivePolicy,
    ExecutionIpcTransportService,
    resolve_execution_ipc_target_id,
)
from stream_kernel.execution.transport.ipc.ipc_lane_routing_service import (
    ExecutionIpcLaneRoutingStore,
    ExecutionIpcLaneRoutingService,
    InMemoryExecutionIpcLaneRoutingService,
)
from stream_kernel.execution.transport.ipc.ipc_transport_service import (
    ExecutionIpcTransportCoordinatorService,
    InMemoryExecutionIpcTransportAdapter,
)
from stream_kernel.observability.domain.debug import DebugMessage
from stream_kernel.platform.services.runtime.debug_buffer import (
    InMemoryRuntimeDebugBufferService,
    RuntimeDebugBufferService,
)
from stream_kernel.platform.services.runtime.control_plane_state import (
    ControlPlaneEventStore,
    ControlPlaneStateService,
    InMemoryControlPlaneStateService,
)

from .ipc_handoff_dispatch_service import (
    DefaultExecutionIpcHandoffDispatchService,
    ExecutionIpcHandoffDispatchService,
)
from .ipc_route_table_service import (
    ExecutionIpcRouteTableService,
    ExecutionIpcRouteTableStore,
    InMemoryExecutionIpcRouteTableService,
)


def ensure_runtime_ipc_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object],
    adapter: ExecutionIpcKvStreamPort | None = None,
    service: ExecutionIpcTransportService | None = None,
) -> None:
    endpoint_registry_store: KVStore | None = None
    if not isinstance(service, ExecutionIpcTransportService):
        if not isinstance(adapter, ExecutionIpcKvStreamPort):
            adapter = _default_execution_ipc_adapter(runtime)
        flow_control = resolve_execution_ipc_flow_control(runtime)
        endpoint_registry_store = _resolve_execution_ipc_endpoint_registry_store(
            adapter=adapter,
            service=None,
        )
        service = ExecutionIpcTransportCoordinatorService(
            adapter=adapter,
            endpoint_registry=endpoint_registry_store,
            flow_control=flow_control,
        )
    endpoint_registry_store = endpoint_registry_store or _resolve_execution_ipc_endpoint_registry_store(
        adapter=getattr(service, "adapter", None),
        service=service,
    )
    if not isinstance(endpoint_registry_store, KVStore):
        endpoint_registry_store = InMemoryKvStore()
    _apply_execution_ipc_polling_settings(runtime=runtime, adapter=service.adapter)
    try:
        injection_registry.register_factory(
            "service",
            RuntimeDebugBufferService,
            lambda: InMemoryRuntimeDebugBufferService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "stream",
            DebugMessage,
            lambda: _NoOpDebugStreamSink(),
            is_async=True,
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "kv",
            ExecutionIpcEndpointRegistry,
            lambda _store=endpoint_registry_store: _store,
        )
    except InjectionRegistryError:
        pass
    for contract in {ExecutionIpcTransportService, type(service)}:
        try:
            injection_registry.register_factory(
                "service",
                contract,
                lambda _service=service: _service,
                replace=True,
            )
        except InjectionRegistryError:
            continue

    try:
        injection_registry.register_factory(
            "kv_stream",
            ExecutionIpcKvStreamPort,
            lambda _adapter=service.adapter: _adapter,
        )
    except InjectionRegistryError:
        pass

    for lane in (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    ):
        try:
            injection_registry.register_factory(
                "kv_stream",
                ExecutionIpcKvStreamPort,
                lambda _adapter=service.adapter: _adapter,
                qualifier=lane,
            )
        except InjectionRegistryError:
            continue

    try:
        injection_registry.register_factory(
            "ipc",
            ExecutionIpcPort,
            lambda _service=service: _service.build_port(),
        )
    except InjectionRegistryError:
        pass

    control_policy = ExecutionIpcReceivePolicy(buffer_enabled=False, batch_max_items=1, flush_interval_ms=0)
    try:
        injection_registry.register_factory(
            "ipc",
            ExecutionIpcPort,
            lambda _service=service, _policy=control_policy: _service.build_port(
                target_id="control",
                receive_policy=_policy,
            ),
            qualifier="control",
        )
    except InjectionRegistryError:
        pass

    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return
    process_groups = platform.get("process_groups", [])
    if not isinstance(process_groups, list):
        return
    buffer_defaults, per_group = _resolve_execution_ipc_buffer_settings(runtime)
    for group in process_groups:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if not isinstance(name, str) or not name:
            continue
        target_id = resolve_execution_ipc_target_id(name)
        if target_id is None:
            continue
        policy = _resolve_execution_ipc_group_policy(
            group_name=name,
            defaults=buffer_defaults,
            per_group=per_group,
        )
        try:
            injection_registry.register_factory(
                "ipc",
                ExecutionIpcPort,
                lambda _service=service, _policy=policy, _target=target_id: _service.build_port(
                    target_id=_target,
                    receive_policy=_policy,
                ),
                qualifier=name,
            )
        except InjectionRegistryError:
            continue


def ensure_runtime_ipc_handoff_bindings(
    *,
    injection_registry: InjectionRegistry,
    runtime: dict[str, object] | None = None,
) -> None:
    _require_execution_ipc_transport_service_binding(injection_registry=injection_registry)
    _ensure_control_plane_state_service_binding(injection_registry=injection_registry)

    store = InMemoryKvStore()
    try:
        injection_registry.register_factory(
            "kv",
            ExecutionIpcRouteTableStore,
            lambda _store=store: _store,
        )
    except InjectionRegistryError:
        pass

    route_table = InMemoryExecutionIpcRouteTableService(store=store)
    _preload_execution_ipc_route_table(route_table=route_table, runtime=runtime)
    for contract in {ExecutionIpcRouteTableService, InMemoryExecutionIpcRouteTableService}:
        try:
            injection_registry.register_factory(
                "service",
                contract,
                lambda _service=route_table: _service,
            )
        except InjectionRegistryError:
            continue

    lane_store = InMemoryKvStore()
    try:
        injection_registry.register_factory(
            "kv",
            ExecutionIpcLaneRoutingStore,
            lambda _store=lane_store: _store,
        )
    except InjectionRegistryError:
        pass
    lane_routing = InMemoryExecutionIpcLaneRoutingService(store=lane_store)
    for contract in {ExecutionIpcLaneRoutingService, InMemoryExecutionIpcLaneRoutingService}:
        try:
            injection_registry.register_factory(
                "service",
                contract,
                lambda _service=lane_routing: _service,
            )
        except InjectionRegistryError:
            continue

    try:
        injection_registry.register_factory(
            "service",
            ExecutionIpcHandoffDispatchService,
            lambda: DefaultExecutionIpcHandoffDispatchService(),
        )
    except InjectionRegistryError:
        pass
    try:
        injection_registry.register_factory(
            "service",
            DefaultExecutionIpcHandoffDispatchService,
            lambda: DefaultExecutionIpcHandoffDispatchService(),
        )
    except InjectionRegistryError:
        pass


def _require_execution_ipc_transport_service_binding(
    *,
    injection_registry: InjectionRegistry,
) -> None:
    bindings = getattr(injection_registry, "_bindings", None)
    if not isinstance(bindings, dict):
        raise InjectionRegistryError("ipc transport bindings registry is unavailable")
    if ("service", ExecutionIpcTransportService, None) in bindings:
        return
    raise InjectionRegistryError(
        "ExecutionIpcTransportService binding is required before handoff bindings. "
        "Call ensure_runtime_ipc_bindings(...) first."
    )


def _ensure_control_plane_state_service_binding(
    *,
    injection_registry: InjectionRegistry,
) -> None:
    bindings = getattr(injection_registry, "_bindings", None)
    if not isinstance(bindings, dict):
        return
    if ("service", ControlPlaneStateService, None) in bindings:
        return
    state_store = InMemoryKvStore()
    try:
        injection_registry.register_factory(
            "kv",
            ControlPlaneEventStore,
            lambda _store=state_store: _store,
        )
    except InjectionRegistryError:
        pass
    state_service = InMemoryControlPlaneStateService(store=state_store)
    for contract in {ControlPlaneStateService, InMemoryControlPlaneStateService}:
        try:
            injection_registry.register_factory(
                "service",
                contract,
                lambda _service=state_service: _service,
            )
        except InjectionRegistryError:
            continue


def resolve_execution_ipc_adapter_from_adapters(
    *,
    adapter_bindings: dict[str, object],
    adapter_instances: dict[str, object],
) -> ExecutionIpcKvStreamPort | None:
    resolved: ExecutionIpcKvStreamPort | None = None
    for role, binding in adapter_bindings.items():
        bindings = binding if isinstance(binding, list) else [binding]
        for port_type, data_type in bindings:
            if port_type != "kv_stream":
                continue
            if not isinstance(data_type, type):
                continue
            if not issubclass(data_type, ExecutionIpcKvStreamPort):
                continue
            instance = adapter_instances.get(role)
            if instance is None:
                continue
            if not isinstance(instance, ExecutionIpcKvStreamPort):
                raise ValueError(
                    f"IPC transport adapter '{role}' must build ExecutionIpcKvStreamPort instances"
                )
            if resolved is not None and resolved is not instance:
                raise ValueError("Multiple IPC transport adapters are configured")
            resolved = instance
    return resolved


def _default_execution_ipc_adapter(runtime: dict[str, object]) -> ExecutionIpcKvStreamPort:
    try:
        if runtime_bootstrap_mode(runtime) == "process_supervisor":
            endpoint_registry_store = InMemoryKvStore()
            return PipeExecutionIpcTransportAdapter(endpoint_registry=endpoint_registry_store)
    except Exception:
        pass
    return InMemoryExecutionIpcTransportAdapter()


def _resolve_execution_ipc_endpoint_registry_store(
    *,
    adapter: object | None,
    service: object | None,
) -> KVStore | None:
    candidate = getattr(service, "endpoint_registry", None) if service is not None else None
    if isinstance(candidate, KVStore):
        return candidate
    candidate = getattr(adapter, "_endpoint_registry", None) if adapter is not None else None
    if isinstance(candidate, KVStore):
        return candidate
    return None


def _resolve_execution_ipc_buffer_settings(
    runtime: dict[str, object],
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    defaults: dict[str, object] = {
        "enabled": True,
        "batch_max_items": 64,
        "flush_interval_ms": 20,
    }
    per_group: dict[str, dict[str, object]] = {}
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return (defaults, per_group)
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return (defaults, per_group)
    buffer_cfg = execution_ipc.get("buffer", {})
    if not isinstance(buffer_cfg, dict):
        return (defaults, per_group)
    enabled = buffer_cfg.get("enabled")
    if isinstance(enabled, bool):
        defaults["enabled"] = enabled
    batch_max_items = buffer_cfg.get("batch_max_items")
    if isinstance(batch_max_items, int) and batch_max_items > 0:
        defaults["batch_max_items"] = batch_max_items
    flush_interval_ms = buffer_cfg.get("flush_interval_ms")
    if isinstance(flush_interval_ms, int) and flush_interval_ms >= 0:
        defaults["flush_interval_ms"] = flush_interval_ms
    raw_per_group = buffer_cfg.get("per_group", {})
    if isinstance(raw_per_group, dict):
        per_group = {
            name: dict(cfg)
            for name, cfg in raw_per_group.items()
            if isinstance(name, str) and name and isinstance(cfg, dict)
        }
    return (defaults, per_group)


def _apply_execution_ipc_polling_settings(
    *,
    runtime: dict[str, object],
    adapter: ExecutionIpcKvStreamPort,
) -> None:
    configure = getattr(adapter, "configure_polling", None)
    if not callable(configure):
        return
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return
    poll_mode = execution_ipc.get("poll_mode")
    poll_interval_ms = execution_ipc.get("poll_interval_ms")
    if poll_mode is None and poll_interval_ms is None:
        return
    configure(poll_mode=poll_mode, poll_interval_ms=poll_interval_ms)


def _resolve_execution_ipc_group_policy(
    *,
    group_name: str,
    defaults: dict[str, object],
    per_group: dict[str, dict[str, object]],
) -> ExecutionIpcReceivePolicy:
    enabled = bool(defaults.get("enabled", True))
    batch_max_items = int(defaults.get("batch_max_items", 64))
    flush_interval_ms = int(defaults.get("flush_interval_ms", 20))
    override = per_group.get(group_name)
    if isinstance(override, dict):
        if isinstance(override.get("enabled"), bool):
            enabled = bool(override["enabled"])
        if isinstance(override.get("batch_max_items"), int):
            batch_max_items = max(1, int(override["batch_max_items"]))
        if isinstance(override.get("flush_interval_ms"), int):
            flush_interval_ms = max(0, int(override["flush_interval_ms"]))
    return ExecutionIpcReceivePolicy(
        buffer_enabled=enabled,
        batch_max_items=batch_max_items,
        flush_interval_ms=flush_interval_ms,
    )


class _NoOpDebugStreamSink:
    def emit(self, _payload: object) -> None:
        return

    async def emit_async(self, _payload: object) -> None:
        return


def _preload_execution_ipc_route_table(
    *,
    route_table: InMemoryExecutionIpcRouteTableService,
    runtime: dict[str, object] | None,
) -> None:
    if not isinstance(runtime, dict):
        return
    snapshot = _route_table_snapshot_from_runtime(runtime)
    if not snapshot:
        return
    route_table.preload_snapshot(routes=snapshot)


def _route_table_snapshot_from_runtime(runtime: dict[str, object]) -> dict[str, str]:
    groups = _runtime_process_groups_with_observability(runtime)
    snapshot: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        workers = group.get("workers")
        worker_count = int(workers) if isinstance(workers, int) and workers > 0 else 1
        nodes = group.get("nodes")
        if not isinstance(nodes, list):
            continue
        target_id = f"{group_name}#1" if worker_count >= 1 else None
        if not isinstance(target_id, str):
            continue
        for node_name in nodes:
            if not isinstance(node_name, str) or not node_name:
                continue
            snapshot.setdefault(node_name, target_id)
    return snapshot


def _runtime_process_groups_with_observability(runtime: dict[str, object]) -> list[dict[str, object]]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    raw_groups = platform.get("process_groups", [])
    groups: list[dict[str, object]] = [
        dict(item) for item in raw_groups if isinstance(item, dict)
    ]
    observability_group = _observability_service_group(runtime)
    if observability_group is None:
        return groups
    group_name = observability_group.get("name")
    if not isinstance(group_name, str) or not group_name:
        return groups
    for existing in groups:
        if existing.get("name") != group_name:
            continue
        merged_nodes = _merge_unique_node_names(
            existing=existing.get("nodes"),
            extra=observability_group.get("nodes"),
        )
        existing["nodes"] = merged_nodes
        if not isinstance(existing.get("workers"), int) or int(existing["workers"]) <= 0:
            existing["workers"] = int(observability_group.get("workers", 1))
        return groups
    groups.append(observability_group)
    return groups


def _observability_service_group(runtime: dict[str, object]) -> dict[str, object] | None:
    role = runtime.get("__process_role")
    if isinstance(role, str) and role == "observability_worker":
        return None
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return None
    service_process = observability.get("service_process")
    if not isinstance(service_process, dict):
        service_process = observability.get("service_worker")
    if not isinstance(service_process, dict):
        return None
    if service_process.get("enabled") is not True:
        return None
    group_name = service_process.get("group_name")
    if not isinstance(group_name, str) or not group_name:
        group_name = "system.observability"
    workers = service_process.get("workers", 1)
    if not isinstance(workers, int) or workers <= 0:
        workers = 1
    nodes = _observability_service_nodes(
        service_process=service_process,
        observability=observability,
    )
    if not nodes:
        return None
    return {
        "name": group_name,
        "workers": workers,
        "nodes": nodes,
    }


def _observability_service_nodes(
    *,
    service_process: dict[str, object],
    observability: dict[str, object],
) -> list[str]:
    configured = service_process.get("nodes")
    if isinstance(configured, list):
        explicit = [item for item in configured if isinstance(item, str) and item]
        if explicit:
            return explicit
    nodes: list[str] = []
    if _has_enabled_exporters(observability, "tracing"):
        nodes.append("system.obs.trace_dispatch")
    if _has_enabled_exporters(observability, "logging"):
        nodes.append("system.obs.log_dispatch")
    if _has_enabled_exporter_kind(observability, "logging", "redis_debug"):
        nodes.append("system.obs.debug_dispatch")
    if _has_enabled_exporters(observability, "telemetry"):
        nodes.append("system.obs.metric_dispatch")
    if _has_enabled_exporters(observability, "monitoring"):
        nodes.extend(
            [
                "system.obs.monitor_dispatch",
                "system.obs.monitoring_metrics_dispatch",
            ]
        )
    worker_queue_telemetry = observability.get("worker_queue_telemetry", {})
    if isinstance(worker_queue_telemetry, dict) and worker_queue_telemetry.get("enabled") is True:
        nodes.append("system.obs.worker_queue_dispatch")
    return _unique_non_empty_strings(nodes)


def _merge_unique_node_names(*, existing: object, extra: object) -> list[str]:
    base = existing if isinstance(existing, list) else []
    tail = extra if isinstance(extra, list) else []
    return _unique_non_empty_strings([*base, *tail])


def _unique_non_empty_strings(values: list[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _has_enabled_exporters(observability: dict[str, object], section: str) -> bool:
    channel = observability.get(section)
    if not isinstance(channel, dict):
        return False
    exporters = channel.get("exporters", [])
    if not isinstance(exporters, list):
        return False
    return any(
        isinstance(exporter, dict) and exporter.get("enabled", True) is not False
        for exporter in exporters
    )


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


__all__ = [
    "ensure_runtime_ipc_bindings",
    "ensure_runtime_ipc_handoff_bindings",
    "resolve_execution_ipc_adapter_from_adapters",
]
