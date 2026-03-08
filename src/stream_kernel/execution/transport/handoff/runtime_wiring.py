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
) -> None:
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


__all__ = [
    "ensure_runtime_ipc_bindings",
    "ensure_runtime_ipc_handoff_bindings",
    "resolve_execution_ipc_adapter_from_adapters",
]
