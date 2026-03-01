from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Iterable

from stream_kernel.adapters.contracts import adapter, get_adapter_meta
from stream_kernel.application_context.service import discover_services
from stream_kernel.kernel.discovery import discover_nodes
from stream_kernel.platform.discovery import platform_discovery_modules
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoverySourceAdapter,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryEntityRecord,
    discovery_entity_sort_key,
)


@dataclass(slots=True)
class ScopedControlPlaneDiscoverySourceAdapter(ControlPlaneDiscoverySourceAdapter):
    source_scope: str
    roots: tuple[str, ...]
    _records_cache: dict[tuple[str, ...], tuple[ControlPlaneDiscoveryEntityRecord, ...]] = field(
        default_factory=dict
    )

    def next_batch(
        self,
        *,
        runtime: dict[str, object],
        cursor: int,
        limit: int,
    ) -> tuple[tuple[ControlPlaneDiscoveryEntityRecord, ...], int | None]:
        if not isinstance(cursor, int) or cursor < 0:
            raise ValueError("control plane discovery source adapter: cursor must be an integer >= 0")
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("control plane discovery source adapter: limit must be an integer > 0")

        resolved_roots = self._resolve_roots(runtime)
        records = self._records_for_roots(resolved_roots)
        if cursor >= len(records):
            return tuple(), None
        upper = min(len(records), cursor + limit)
        batch = records[cursor:upper]
        next_cursor = upper if upper < len(records) else None
        return (batch, next_cursor)

    def _resolve_roots(self, runtime: dict[str, object]) -> tuple[str, ...]:
        # Adapter-level defaults live in code; runtime config overrides when provided.
        if not isinstance(runtime, dict):
            return self.roots
        platform = runtime.get("platform")
        if not isinstance(platform, dict):
            return self.roots
        discovery = platform.get("discovery")
        if not isinstance(discovery, dict):
            return self.roots
        key = "platform_modules" if self.source_scope == "platform" else "project_modules"
        configured = discovery.get(key)
        if not isinstance(configured, list):
            return self.roots
        roots = [item for item in configured if isinstance(item, str) and item]
        if not roots:
            return self.roots
        return tuple(sorted(dict.fromkeys(roots)))

    def _records_for_roots(self, roots: tuple[str, ...]) -> tuple[ControlPlaneDiscoveryEntityRecord, ...]:
        cached = self._records_cache.get(roots)
        if isinstance(cached, tuple):
            return cached
        modules = self._collect_modules(roots)
        records = self._discover_records(modules)
        ordered = tuple(sorted(records, key=discovery_entity_sort_key))
        self._records_cache[roots] = ordered
        return ordered

    def _collect_modules(self, roots: tuple[str, ...]) -> list[ModuleType]:
        queue = list(roots)
        visited: set[str] = set()
        modules: list[ModuleType] = []
        while queue:
            module_name = queue.pop(0)
            if module_name in visited:
                continue
            visited.add(module_name)
            module = self._safe_import(module_name)
            if module is None:
                continue
            modules.append(module)
            children = self._iter_submodule_names(module_name, module)
            for child_name in sorted(children):
                if child_name not in visited:
                    queue.append(child_name)
        return modules

    def _safe_import(self, module_name: str) -> ModuleType | None:
        try:
            return self._import_module(module_name)
        except Exception:
            return None

    def _import_module(self, module_name: str) -> ModuleType:
        return importlib.import_module(module_name)

    def _iter_submodule_names(self, module_name: str, module: ModuleType) -> tuple[str, ...]:
        module_path = getattr(module, "__path__", None)
        if module_path is None:
            return tuple()
        discovered: list[str] = []
        for item in pkgutil.iter_modules(module_path):
            discovered.append(f"{module_name}.{item.name}")
        return tuple(discovered)

    def _discover_records(self, modules: Iterable[ModuleType]) -> list[ControlPlaneDiscoveryEntityRecord]:
        records: list[ControlPlaneDiscoveryEntityRecord] = []
        module_list = list(modules)
        records.extend(self._discover_node_records(module_list))
        records.extend(self._discover_service_records(module_list))
        records.extend(self._discover_adapter_records(module_list))
        return records

    def _discover_node_records(self, modules: list[ModuleType]) -> list[ControlPlaneDiscoveryEntityRecord]:
        records: list[ControlPlaneDiscoveryEntityRecord] = []
        for node_def in discover_nodes(modules):
            target = node_def.target
            target_module = getattr(target, "__module__", "")
            qualname = getattr(target, "__qualname__", getattr(target, "__name__", type(target).__name__))
            if not isinstance(target_module, str) or not target_module:
                continue
            records.append(
                ControlPlaneDiscoveryEntityRecord(
                    entity_kind="node",
                    entity_id=f"{target_module}:{qualname}",
                    source_scope=self.source_scope,
                    module=target_module,
                    qualname=qualname,
                    meta={
                        "name": node_def.meta.name,
                        "stage": node_def.meta.stage,
                        "consumes": [self._type_name(item) for item in node_def.meta.consumes],
                        "emits": [self._type_name(item) for item in node_def.meta.emits],
                    },
                )
            )
        return records

    def _discover_service_records(self, modules: list[ModuleType]) -> list[ControlPlaneDiscoveryEntityRecord]:
        records: list[ControlPlaneDiscoveryEntityRecord] = []
        for service_cls in discover_services(modules):
            module_name = getattr(service_cls, "__module__", "")
            qualname = getattr(service_cls, "__qualname__", service_cls.__name__)
            if not isinstance(module_name, str) or not module_name:
                continue
            meta = getattr(service_cls, "__service_meta__", None)
            service_name = getattr(meta, "name", qualname)
            records.append(
                ControlPlaneDiscoveryEntityRecord(
                    entity_kind="service",
                    entity_id=f"{module_name}:{qualname}",
                    source_scope=self.source_scope,
                    module=module_name,
                    qualname=qualname,
                    meta={"name": service_name},
                )
            )
        return records

    def _discover_adapter_records(self, modules: list[ModuleType]) -> list[ControlPlaneDiscoveryEntityRecord]:
        records: list[ControlPlaneDiscoveryEntityRecord] = []
        discovered: dict[str, object] = {}
        for module in modules:
            module_name = getattr(module, "__name__", "")
            if not isinstance(module_name, str) or not module_name:
                continue
            for value in module.__dict__.values():
                meta = get_adapter_meta(value)
                if meta is None:
                    continue
                if meta.name in discovered and discovered[meta.name] is not value:
                    continue
                discovered[meta.name] = value
                qualname = getattr(value, "__qualname__", getattr(value, "__name__", meta.name))
                records.append(
                    ControlPlaneDiscoveryEntityRecord(
                        entity_kind="adapter",
                        entity_id=f"{module_name}:{qualname}",
                        source_scope=self.source_scope,
                        module=module_name,
                        qualname=qualname,
                        meta={
                            "name": meta.name,
                            "kind": meta.kind or "",
                            "execution_mode": meta.execution_mode,
                            "consumes": [self._type_name(item) for item in meta.consumes],
                            "emits": [self._type_name(item) for item in meta.emits],
                        },
                    )
                )
        return records

    @staticmethod
    def _type_name(value: object) -> str:
        if isinstance(value, type):
            return value.__name__
        return str(value)


@adapter(
    name="platform_discovery_source_adapter",
    kind="control_plane.discovery.source.platform",
    consumes=[ControlPlaneDiscoveryBatchRequestedEvent],
    emits=[ControlPlaneDiscoveryEntityRecord],
)
def platform_discovery_source_adapter(settings: dict[str, object]) -> ControlPlaneDiscoverySourceAdapter:
    _ = settings
    return ScopedControlPlaneDiscoverySourceAdapter(
        source_scope="platform",
        roots=tuple(sorted(dict.fromkeys(platform_discovery_modules()))),
    )


@adapter(
    name="project_discovery_source_adapter",
    kind="control_plane.discovery.source.project",
    consumes=[ControlPlaneDiscoveryBatchRequestedEvent],
    emits=[ControlPlaneDiscoveryEntityRecord],
)
def project_discovery_source_adapter(settings: dict[str, object]) -> ControlPlaneDiscoverySourceAdapter:
    roots: tuple[str, ...] = tuple()
    project = settings.get("project_modules")
    if isinstance(project, list):
        resolved = [item for item in project if isinstance(item, str) and item]
        roots = tuple(sorted(dict.fromkeys(resolved)))
    return ScopedControlPlaneDiscoverySourceAdapter(
        source_scope="project",
        roots=roots,
    )


__all__ = [
    "ScopedControlPlaneDiscoverySourceAdapter",
    "platform_discovery_source_adapter",
    "project_discovery_source_adapter",
]
