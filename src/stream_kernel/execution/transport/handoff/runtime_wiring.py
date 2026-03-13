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
    ExecutionIpcMessage,
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
) -> None:
    _ensure_execution_ipc_transport_service_binding(injection_registry=injection_registry)
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


def _ensure_execution_ipc_transport_service_binding(
    *,
    injection_registry: InjectionRegistry,
) -> None:
    bindings = getattr(injection_registry, "_bindings", None)
    if not isinstance(bindings, dict):
        return
    if ("service", ExecutionIpcTransportService, None) in bindings:
        return
    kv_stream_binding = bindings.get(("kv_stream", ExecutionIpcKvStreamPort, None))
    if kv_stream_binding is None:
        return
    adapter_factory = getattr(kv_stream_binding, "factory", None)
    if not callable(adapter_factory):
        return
    try:
        adapter = adapter_factory()
    except Exception:
        return
    if isinstance(adapter, ExecutionIpcKvStreamPort):
        endpoint_registry_store = InMemoryKvStore()
        flow_control = resolve_execution_ipc_flow_control({})
        try:
            injection_registry.register_factory(
                "kv",
                ExecutionIpcEndpointRegistry,
                lambda _store=endpoint_registry_store: _store,
            )
        except InjectionRegistryError:
            pass
        try:
            injection_registry.register_factory(
                "service",
                ExecutionIpcTransportService,
                lambda _adapter=adapter, _store=endpoint_registry_store, _flow=flow_control: ExecutionIpcTransportCoordinatorService(
                    adapter=_adapter,
                    endpoint_registry=_store,
                    flow_control=_flow,
                ),
            )
        except InjectionRegistryError:
            return
        return
    if callable(getattr(adapter, "send", None)):
        try:
            injection_registry.register_factory(
                "service",
                ExecutionIpcTransportService,
                lambda _adapter=adapter: _SendOnlyExecutionIpcTransportService(raw_adapter=_adapter),
            )
        except InjectionRegistryError:
            return


class _SendOnlyExecutionIpcTransportService(ExecutionIpcTransportService):
    def __init__(self, *, raw_adapter: object) -> None:
        self._raw_adapter = raw_adapter

    def send(
        self,
        target_id: str,
        payload: object,
        *,
        no_reply: bool = False,
    ):
        send = getattr(self._raw_adapter, "send", None)
        if not callable(send):
            raise ConnectionError("send-only ipc adapter is unavailable")
        legacy_target = target_id
        if isinstance(legacy_target, str) and "::" in legacy_target:
            base, _sep, lane = legacy_target.partition("::")
            if lane == EXECUTION_IPC_LANE_DATA and base:
                legacy_target = base
        return send(legacy_target, payload, no_reply=no_reply)

    def recv(self, target_id: str, *, timeout: float | None = None) -> ExecutionIpcMessage | None:
        _ = target_id
        _ = timeout
        return None

    def metrics(self, target_id: str) -> dict[str, object]:
        _ = target_id
        return {}

    def flush_pending(self, target_id: str) -> int:
        _ = target_id
        return 0

    def build_port(
        self,
        *,
        target_id: str | None = None,
        receive_policy: ExecutionIpcReceivePolicy | None = None,
    ) -> ExecutionIpcPort:
        _ = receive_policy
        return ExecutionIpcPort(service=self, target_id=target_id)

    def allocate_local_endpoints(self, target_id: str) -> tuple[object, object]:
        _ = target_id
        raise ValueError("send-only ipc transport service does not allocate local endpoints")

    def bind_local_endpoint(self, target_id: str, endpoint: object) -> None:
        _ = target_id
        _ = endpoint


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


__all__ = [
    "ensure_runtime_ipc_bindings",
    "ensure_runtime_ipc_handoff_bindings",
    "resolve_execution_ipc_adapter_from_adapters",
]
