from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stream_kernel.adapters.contracts import get_adapter_meta
from stream_kernel.adapters.discovery import discover_adapters
from stream_kernel.application_context.inject import Injected
from stream_kernel.application_context.service import discover_services
from stream_kernel.config.loader import load_yaml_config
from stream_kernel.config.validator import validate_newgen_config
from stream_kernel.execution.orchestration.builder import (
    ensure_platform_discovery_modules,
    load_discovery_modules,
)
from stream_kernel.execution.orchestration.observability_system_nodes import (
    _resolve_system_nodes_config,
)
from stream_kernel.kernel.discovery import discover_nodes

_ROOT_SYSTEM_NODE_NAMES: tuple[str, ...] = (
    "system.cp.root_bootstrap",
    "system.cp.root_config_stream",
    "system.cp.discovery_pump",
    "system.cp.discovery_apply",
    "system.cp.discovery_finalize",
    "system.cp.system_config_apply",
    "system.cp.observability_config_apply",
    "system.cp.node_config_apply",
    "system.cp.config_apply_barrier",
    "system.cp.startup_barrier",
    "system.cp.dag_assembly",
    "system.cp.init_plan",
    "system.cp.start_work_dispatch",
    "system.transport.handoff.ipc_dispatch",
    "system.transport.handoff.observability_dispatch",
)

_LEAF_SYSTEM_NODE_NAMES: tuple[str, ...] = (
    "system.cp.leaf_bootstrap",
    "system.cp.leaf_discovery",
    "system.cp.leaf_snapshot_apply",
    "system.cp.leaf_apply_config",
    "system.cp.leaf_start_work",
    "system.cp.leaf_boundary_execute",
    "system.cp.leaf_stop",
)

_OBSERVABILITY_GROUP_NAME = "system.observability"
_IPC_LANES = ("control", "data", "trace", "log", "metric")
_DEFAULT_CONFIG_PATH = "src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml"


@dataclass(frozen=True, slots=True)
class _InjectedDependency:
    port_type: str
    qualifier: str | None
    contract_name: str
    contract_type: type[Any]


@dataclass(frozen=True, slots=True)
class _DiscoveredNode:
    name: str
    module: str
    stage: str
    origin: str


@dataclass(frozen=True, slots=True)
class _NodeRuntime:
    target: object
    dependencies: tuple[_InjectedDependency, ...]


@dataclass(frozen=True, slots=True)
class _ServiceRuntime:
    name: str
    class_name: str
    module: str
    origin: str
    cls: type[object]
    dependencies: tuple[_InjectedDependency, ...]


@dataclass(frozen=True, slots=True)
class _AdapterRuntime:
    name: str
    module: str
    origin: str
    kind: str | None
    execution_mode: str
    binds: tuple[tuple[str, type[Any]], ...]


def build_topology_snapshot(config_path: str | Path | None = None) -> dict[str, object]:
    resolved_path = _resolve_config_path(config_path)
    raw = load_yaml_config(resolved_path)
    validated = validate_newgen_config(raw)
    runtime = validated.get("runtime", {})
    runtime_map = runtime if isinstance(runtime, dict) else {}
    discovery_module_names = _resolve_discovery_module_names(runtime_map)
    modules = load_discovery_modules(discovery_module_names)

    discovered_nodes, node_runtime_map = _discover_nodes(modules)
    discovered_node_map = {item.name: item for item in discovered_nodes}
    discovered_services, service_runtime_map = _discover_services(modules)
    discovered_adapters, adapter_runtime_map = _discover_adapters(modules)
    active_adapter_names = _resolve_active_adapter_names(validated)

    process_groups = _resolve_process_groups(runtime_map)
    observability_group = _resolve_observability_group(runtime_map)
    known_group_names = {
        str(group.get("group_name"))
        for group in process_groups
        if isinstance(group, dict)
    }
    if (
        observability_group is not None
        and str(observability_group.get("group_name")) not in known_group_names
    ):
        process_groups.append(observability_group)

    processes = _build_processes(
        process_groups=process_groups,
        discovered_node_map=discovered_node_map,
        node_runtime_map=node_runtime_map,
        service_runtime_map=service_runtime_map,
        adapter_runtime_map=adapter_runtime_map,
        active_adapter_names=active_adapter_names,
    )
    pipes = _build_ipc_pipes(processes=processes, runtime=runtime_map)

    return {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            "config_path": str(resolved_path),
            "discovery_modules": list(discovery_module_names),
            "process_count": len(processes),
            "pipe_count": len(pipes),
            "transport": _resolve_transport_name(runtime_map),
        },
        "processes": processes,
        "pipes": pipes,
        "catalog": {
            "nodes": [asdict(item) for item in discovered_nodes],
            "services": discovered_services,
            "adapters": discovered_adapters,
            "stores": _collect_catalog_stores(processes),
        },
    }


def _resolve_config_path(config_path: str | Path | None) -> Path:
    candidate = Path(_DEFAULT_CONFIG_PATH) if config_path is None else Path(config_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _resolve_discovery_module_names(runtime: dict[str, object]) -> list[str]:
    raw_modules = runtime.get("discovery_modules", [])
    modules: list[str] = []
    if isinstance(raw_modules, list):
        modules = [item for item in raw_modules if isinstance(item, str) and item]
    ensure_platform_discovery_modules(modules)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in modules:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _discover_nodes(modules: list[Any]) -> tuple[list[_DiscoveredNode], dict[str, _NodeRuntime]]:
    discovered_rows: list[_DiscoveredNode] = []
    runtime_map: dict[str, _NodeRuntime] = {}
    for node_def in discover_nodes(modules):
        name = node_def.meta.name
        module = getattr(node_def.target, "__module__", "")
        stage = node_def.meta.stage or ""
        discovered_rows.append(
            _DiscoveredNode(
                name=name,
                module=module,
                stage=stage,
                origin=_origin_from_module(module),
            )
        )
        runtime_map[name] = _NodeRuntime(
            target=node_def.target,
            dependencies=_extract_injected_dependencies(node_def.target),
        )
    discovered_rows.sort(key=lambda item: (item.origin, item.name))
    return discovered_rows, runtime_map


def _discover_services(
    modules: list[Any],
) -> tuple[list[dict[str, object]], dict[str, _ServiceRuntime]]:
    rows: list[dict[str, object]] = []
    runtime_map: dict[str, _ServiceRuntime] = {}
    for service_cls in discover_services(modules):
        module = getattr(service_cls, "__module__", "")
        service_meta = getattr(service_cls, "__service_meta__", None)
        service_name = getattr(service_meta, "name", getattr(service_cls, "__name__", "<service>"))
        class_name = getattr(service_cls, "__name__", "<service>")
        origin = _origin_from_module(module)
        runtime = _ServiceRuntime(
            name=service_name,
            class_name=class_name,
            module=module,
            origin=origin,
            cls=service_cls,
            dependencies=_extract_injected_dependencies(service_cls),
        )
        runtime_map[service_name] = runtime
        rows.append(
            {
                "name": service_name,
                "class_name": class_name,
                "module": module,
                "origin": origin,
            }
        )
    rows.sort(key=lambda item: (str(item["origin"]), str(item["name"])))
    return rows, runtime_map


def _discover_adapters(
    modules: list[Any],
) -> tuple[list[dict[str, object]], dict[str, _AdapterRuntime]]:
    rows: list[dict[str, object]] = []
    runtime_map: dict[str, _AdapterRuntime] = {}
    discovered = discover_adapters(modules)
    for adapter_name, factory in discovered.items():
        meta = get_adapter_meta(factory)
        module = getattr(factory, "__module__", "")
        origin = _origin_from_module(module)
        binds: list[tuple[str, type[Any]]] = []
        for bind in list(getattr(meta, "binds", ())) if meta is not None else []:
            if not isinstance(bind, tuple) or len(bind) != 2:
                continue
            port_type, contract = bind
            if not isinstance(port_type, str) or not isinstance(contract, type):
                continue
            binds.append((port_type, contract))
        runtime_map[adapter_name] = _AdapterRuntime(
            name=adapter_name,
            module=module,
            origin=origin,
            kind=getattr(meta, "kind", None) if meta is not None else None,
            execution_mode=getattr(meta, "execution_mode", "sync") if meta is not None else "sync",
            binds=tuple(binds),
        )
        rows.append(
            {
                "name": adapter_name,
                "kind": getattr(meta, "kind", None) if meta is not None else None,
                "execution_mode": (
                    getattr(meta, "execution_mode", "sync")
                    if meta is not None
                    else "sync"
                ),
                "module": module,
                "origin": origin,
                "binds": [
                    {
                        "port_type": bind_port,
                        "contract": _type_name(bind_type),
                    }
                    for bind_port, bind_type in binds
                ],
            }
        )
    rows.sort(key=lambda item: (str(item["origin"]), str(item["name"])))
    return rows, runtime_map


def _extract_injected_dependencies(target: object) -> tuple[_InjectedDependency, ...]:
    if not isinstance(target, type):
        return ()

    found: dict[tuple[str, str | None, str], _InjectedDependency] = {}

    def _collect(value: object) -> None:
        if not isinstance(value, Injected):
            return
        port_type = value.port_type
        qualifier = value.qualifier
        contract = value.data_type
        if not isinstance(port_type, str):
            return
        if not isinstance(contract, type):
            return
        dep = _InjectedDependency(
            port_type=port_type,
            qualifier=qualifier if isinstance(qualifier, str) and qualifier else None,
            contract_name=_type_name(contract),
            contract_type=contract,
        )
        found[(dep.port_type, dep.qualifier, dep.contract_name)] = dep

    for value in target.__dict__.values():
        _collect(value)

    if is_dataclass(target):
        for field in fields(target):
            if field.default is not MISSING:
                _collect(field.default)
            if field.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                try:
                    candidate = field.default_factory()  # type: ignore[misc]
                except Exception:
                    continue
                _collect(candidate)

    return tuple(
        sorted(
            found.values(),
            key=lambda dep: (dep.port_type, dep.contract_name, dep.qualifier or ""),
        )
    )


def _resolve_active_adapter_names(config: dict[str, object]) -> set[str]:
    adapters = config.get("adapters", {})
    if not isinstance(adapters, dict):
        return set()
    return {
        key
        for key in adapters
        if isinstance(key, str) and key
    }


def _resolve_process_groups(runtime: dict[str, object]) -> list[dict[str, object]]:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return []
    groups = platform.get("process_groups", [])
    if not isinstance(groups, list):
        return []
    resolved: list[dict[str, object]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            continue
        workers = group.get("workers", 1)
        worker_count = int(workers) if isinstance(workers, int) and workers > 0 else 1
        nodes = group.get("nodes", [])
        node_names = [item for item in nodes if isinstance(item, str) and item]
        resolved.append(
            {
                "group_name": group_name,
                "workers": worker_count,
                "nodes": node_names,
                "kind": "business",
            }
        )
    return resolved


def _resolve_observability_group(runtime: dict[str, object]) -> dict[str, object] | None:
    observability = runtime.get("observability", {})
    if not isinstance(observability, dict):
        return None
    worker_cfg = observability.get("service_worker")
    if not isinstance(worker_cfg, dict):
        return None
    if worker_cfg.get("enabled") is not True:
        return None
    workers_raw = worker_cfg.get("workers", 1)
    workers = workers_raw if isinstance(workers_raw, int) and workers_raw > 0 else 1
    nodes_cfg = _resolve_system_nodes_config(observability)
    node_names = [
        item.get("kind")
        for item in nodes_cfg
        if isinstance(item, dict) and isinstance(item.get("kind"), str)
    ]
    return {
        "group_name": _OBSERVABILITY_GROUP_NAME,
        "workers": workers,
        "nodes": node_names,
        "kind": "observability",
    }


def _build_processes(
    *,
    process_groups: list[dict[str, object]],
    discovered_node_map: dict[str, _DiscoveredNode],
    node_runtime_map: dict[str, _NodeRuntime],
    service_runtime_map: dict[str, _ServiceRuntime],
    adapter_runtime_map: dict[str, _AdapterRuntime],
    active_adapter_names: set[str],
) -> list[dict[str, object]]:
    processes: list[dict[str, object]] = []

    processes.append(
        _materialize_process(
            process_id="root",
            group_name="root",
            role="root",
            group_kind="root",
            node_names=tuple(_ROOT_SYSTEM_NODE_NAMES),
            discovered_node_map=discovered_node_map,
            node_runtime_map=node_runtime_map,
            service_runtime_map=service_runtime_map,
            adapter_runtime_map=adapter_runtime_map,
            active_adapter_names=active_adapter_names,
        )
    )

    for group in process_groups:
        group_name = str(group["group_name"])
        workers = int(group["workers"])
        configured_nodes = list(group["nodes"])
        group_kind = str(group.get("kind", "business"))
        for slot in range(workers):
            worker_id = f"{group_name}#{slot + 1}"
            names = tuple(dict.fromkeys([*_LEAF_SYSTEM_NODE_NAMES, *configured_nodes]))
            processes.append(
                _materialize_process(
                    process_id=worker_id,
                    group_name=group_name,
                    role="worker",
                    group_kind=group_kind,
                    node_names=names,
                    discovered_node_map=discovered_node_map,
                    node_runtime_map=node_runtime_map,
                    service_runtime_map=service_runtime_map,
                    adapter_runtime_map=adapter_runtime_map,
                    active_adapter_names=active_adapter_names,
                )
            )

    return processes


def _materialize_process(
    *,
    process_id: str,
    group_name: str,
    role: str,
    group_kind: str,
    node_names: tuple[str, ...],
    discovered_node_map: dict[str, _DiscoveredNode],
    node_runtime_map: dict[str, _NodeRuntime],
    service_runtime_map: dict[str, _ServiceRuntime],
    adapter_runtime_map: dict[str, _AdapterRuntime],
    active_adapter_names: set[str],
) -> dict[str, object]:
    nodes = _materialize_nodes(
        process_id=process_id,
        names=node_names,
        discovered_node_map=discovered_node_map,
        node_runtime_map=node_runtime_map,
    )
    services, adapters, stores, links = _resolve_process_components(
        process_id=process_id,
        nodes=nodes,
        node_runtime_map=node_runtime_map,
        service_runtime_map=service_runtime_map,
        adapter_runtime_map=adapter_runtime_map,
        active_adapter_names=active_adapter_names,
    )
    return {
        "process_id": process_id,
        "group_name": group_name,
        "role": role,
        "group_kind": group_kind,
        "nodes": nodes,
        "services": services,
        "adapters": adapters,
        "stores": stores,
        "links": links,
        "buffers": _process_buffer_descriptors(process_id),
        "entity_ids": [
            *[item["id"] for item in nodes],
            *[item["id"] for item in services],
            *[item["id"] for item in adapters],
            *[item["id"] for item in stores],
        ],
    }


def _materialize_nodes(
    *,
    process_id: str,
    names: tuple[str, ...],
    discovered_node_map: dict[str, _DiscoveredNode],
    node_runtime_map: dict[str, _NodeRuntime],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in names:
        node_id = f"{process_id}:node:{_sanitize_identifier(name)}"
        discovered = discovered_node_map.get(name)
        runtime = node_runtime_map.get(name)
        dependencies = [
            {
                "port_type": dep.port_type,
                "contract": dep.contract_name,
                "qualifier": dep.qualifier,
            }
            for dep in runtime.dependencies
        ] if runtime is not None else []

        if discovered is None:
            rows.append(
                {
                    "id": node_id,
                    "name": name,
                    "origin": "missing",
                    "module": None,
                    "stage": None,
                    "kind": "business" if not name.startswith("system.") else "system",
                    "dependencies": dependencies,
                }
            )
            continue

        rows.append(
            {
                "id": node_id,
                "name": discovered.name,
                "origin": discovered.origin,
                "module": discovered.module,
                "stage": discovered.stage,
                "kind": "business" if not discovered.name.startswith("system.") else "system",
                "dependencies": dependencies,
            }
        )

    return rows


def _resolve_process_components(
    *,
    process_id: str,
    nodes: list[dict[str, object]],
    node_runtime_map: dict[str, _NodeRuntime],
    service_runtime_map: dict[str, _ServiceRuntime],
    adapter_runtime_map: dict[str, _AdapterRuntime],
    active_adapter_names: set[str],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    services_by_name: dict[str, dict[str, object]] = {}
    adapters_by_name: dict[str, dict[str, object]] = {}
    stores_by_key: dict[tuple[str, str | None], dict[str, object]] = {}
    links: list[dict[str, object]] = []
    seen_links: set[tuple[str, str, str]] = set()

    service_queue: list[str] = []
    seen_service_queue: set[str] = set()

    def _emit_link(source_id: str, target_id: str, link_kind: str) -> None:
        key = (source_id, target_id, link_kind)
        if key in seen_links:
            return
        seen_links.add(key)
        links.append({"from": source_id, "to": target_id, "kind": link_kind})

    def _ensure_service(service_name: str) -> dict[str, object] | None:
        if service_name in services_by_name:
            return services_by_name[service_name]
        runtime = service_runtime_map.get(service_name)
        if runtime is None:
            return None
        row = {
            "id": f"{process_id}:service:{_sanitize_identifier(service_name)}",
            "name": service_name,
            "class_name": runtime.class_name,
            "origin": runtime.origin,
            "module": runtime.module,
            "kind": "service",
        }
        services_by_name[service_name] = row
        if service_name not in seen_service_queue:
            seen_service_queue.add(service_name)
            service_queue.append(service_name)
        return row

    def _ensure_adapter(adapter_name: str) -> dict[str, object] | None:
        if adapter_name in adapters_by_name:
            return adapters_by_name[adapter_name]
        runtime = adapter_runtime_map.get(adapter_name)
        if runtime is None:
            return None
        row = {
            "id": f"{process_id}:adapter:{_sanitize_identifier(adapter_name)}",
            "name": runtime.name,
            "kind": "adapter",
            "adapter_kind": runtime.kind,
            "execution_mode": runtime.execution_mode,
            "origin": runtime.origin,
            "module": runtime.module,
        }
        adapters_by_name[adapter_name] = row
        return row

    def _ensure_store(contract_name: str, qualifier: str | None) -> dict[str, object]:
        key = (contract_name, qualifier)
        if key in stores_by_key:
            return stores_by_key[key]
        label = contract_name if qualifier is None else f"{contract_name}#{qualifier}"
        row = {
            "id": f"{process_id}:store:{_sanitize_identifier(label)}",
            "name": label,
            "contract": contract_name,
            "qualifier": qualifier,
            "kind": "store",
            "origin": "platform",
        }
        stores_by_key[key] = row
        return row

    def _attach_dependency(source_id: str, dep: _InjectedDependency) -> None:
        if dep.port_type == "service":
            for service_name in _resolve_service_candidates(dep, service_runtime_map):
                service_row = _ensure_service(service_name)
                if service_row is not None:
                    _emit_link(source_id, str(service_row["id"]), "service")
            return

        if dep.port_type == "kv":
            store_row = _ensure_store(dep.contract_name, dep.qualifier)
            _emit_link(source_id, str(store_row["id"]), "store")
            return

        for adapter_name in _resolve_adapter_candidates(
            dep,
            adapter_runtime_map=adapter_runtime_map,
            active_adapter_names=active_adapter_names,
        ):
            adapter_row = _ensure_adapter(adapter_name)
            if adapter_row is not None:
                _emit_link(source_id, str(adapter_row["id"]), "adapter")

    for node in nodes:
        node_name = node.get("name")
        node_id = node.get("id")
        if not isinstance(node_name, str) or not isinstance(node_id, str):
            continue

        # Source/sink wrapper nodes commonly encode adapter role as `source:<role>` / `sink:<role>`.
        role_hint = _adapter_role_from_node_name(node_name)
        if role_hint is not None and role_hint in adapter_runtime_map:
            adapter_row = _ensure_adapter(role_hint)
            if adapter_row is not None:
                _emit_link(node_id, str(adapter_row["id"]), "adapter")

        runtime = node_runtime_map.get(node_name)
        if runtime is None:
            continue
        for dep in runtime.dependencies:
            _attach_dependency(node_id, dep)

    processed_services: set[str] = set()
    while service_queue:
        service_name = service_queue.pop(0)
        if service_name in processed_services:
            continue
        processed_services.add(service_name)

        service_row = services_by_name.get(service_name)
        if service_row is None:
            continue
        source_id = str(service_row["id"])
        runtime = service_runtime_map.get(service_name)
        if runtime is None:
            continue
        for dep in runtime.dependencies:
            _attach_dependency(source_id, dep)

    services = sorted(services_by_name.values(), key=lambda row: str(row["name"]))
    adapters = sorted(adapters_by_name.values(), key=lambda row: str(row["name"]))
    stores = sorted(stores_by_key.values(), key=lambda row: str(row["name"]))
    links.sort(key=lambda row: (str(row["from"]), str(row["to"]), str(row["kind"])))
    return services, adapters, stores, links


def _resolve_service_candidates(
    dep: _InjectedDependency,
    service_runtime_map: dict[str, _ServiceRuntime],
) -> list[str]:
    candidates: list[str] = []
    for name, runtime in service_runtime_map.items():
        if runtime.class_name == dep.contract_name or runtime.name == dep.contract_name:
            candidates.append(name)
            continue
        if dep.contract_type is object:
            continue
        if _type_compatible(runtime.cls, dep.contract_type):
            candidates.append(name)
    candidates.sort()
    return candidates


def _resolve_adapter_candidates(
    dep: _InjectedDependency,
    *,
    adapter_runtime_map: dict[str, _AdapterRuntime],
    active_adapter_names: set[str],
) -> list[str]:
    if dep.qualifier and dep.qualifier in adapter_runtime_map:
        return [dep.qualifier]

    matches: list[str] = []
    for name, runtime in adapter_runtime_map.items():
        for bind_port, bind_type in runtime.binds:
            if bind_port != dep.port_type:
                continue
            if _type_compatible(bind_type, dep.contract_type):
                matches.append(name)
                break

    if not matches:
        return []

    matches = sorted(set(matches))
    if not active_adapter_names:
        return matches[:3]

    active_matches = [name for name in matches if name in active_adapter_names]
    if active_matches:
        return active_matches[:3]
    return matches[:3]


def _adapter_role_from_node_name(node_name: str) -> str | None:
    if ":" not in node_name:
        return None
    left, right = node_name.split(":", 1)
    if left not in {"source", "sink"}:
        return None
    return right if right else None


def _type_compatible(candidate: type[Any], contract: type[Any]) -> bool:
    if candidate is contract:
        return True
    if _safe_issubclass(candidate, contract):
        return True
    if _safe_issubclass(contract, candidate):
        return True
    return _type_name(candidate) == _type_name(contract)


def _safe_issubclass(left: type[Any], right: type[Any]) -> bool:
    try:
        return issubclass(left, right)
    except TypeError:
        return False


def _type_name(data_type: type[Any]) -> str:
    name = getattr(data_type, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(data_type)


def _sanitize_identifier(value: str) -> str:
    chunks: list[str] = []
    for char in value:
        if char.isalnum() or char in {"_", "-"}:
            chunks.append(char)
        else:
            chunks.append("_")
    return "".join(chunks)


def _collect_catalog_stores(processes: list[dict[str, object]]) -> list[dict[str, object]]:
    stores: dict[tuple[str, str | None], dict[str, object]] = {}
    for process in processes:
        rows = process.get("stores")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            contract = row.get("contract")
            qualifier = row.get("qualifier")
            if not isinstance(contract, str):
                continue
            key = (contract, qualifier if isinstance(qualifier, str) else None)
            stores[key] = {
                "name": row.get("name"),
                "contract": contract,
                "qualifier": key[1],
            }
    return sorted(stores.values(), key=lambda row: str(row["name"]))


def _process_buffer_descriptors(process_id: str) -> list[dict[str, object]]:
    return [
        {
            "id": f"{process_id}:buffer:recv",
            "name": f"{process_id}:recv_buffer",
            "kind": "recv_buffer",
        },
        {
            "id": f"{process_id}:buffer:outbound",
            "name": f"{process_id}:pending_outbound",
            "kind": "pending_outbound",
        },
    ]


def _build_ipc_pipes(
    *,
    processes: list[dict[str, object]],
    runtime: dict[str, object],
) -> list[dict[str, object]]:
    pipes: list[dict[str, object]] = []
    transport = _resolve_transport_name(runtime)
    for process in processes:
        process_id = process.get("process_id")
        if process_id == "root" or not isinstance(process_id, str):
            continue
        pipes.append(
            {
                "pipe_id": f"pipe:root:{process_id}",
                "from_process": "root",
                "to_process": process_id,
                "transport": transport,
                "lanes": list(_IPC_LANES),
                "buffering": {
                    "root_pending_outbound": True,
                    "leaf_pending_outbound": True,
                    "root_recv_buffer": True,
                    "leaf_recv_buffer": True,
                },
            }
        )
    return pipes


def _resolve_transport_name(runtime: dict[str, object]) -> str:
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        return "ipc_local"
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        return "ipc_local"
    transport = execution_ipc.get("transport", "ipc_local")
    if not isinstance(transport, str) or not transport:
        return "ipc_local"
    return transport


def _origin_from_module(module_name: str) -> str:
    if module_name.startswith("stream_kernel."):
        return "platform"
    return "project"
