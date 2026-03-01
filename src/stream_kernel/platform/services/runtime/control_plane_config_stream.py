from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConfigRecord,
    ControlPlaneConfigStreamCompletedEvent,
    ExecutionGroupConfigRecord,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
)

_CONFIG_RECORDS_KEY = "control_plane.config_stream.records"


class ControlPlaneStartupConfigRegistry(KVStore):
    # KV marker contract for startup config records.
    pass


@runtime_checkable
class ControlPlaneStartupConfigStore(Protocol):
    def append(self, record: ControlPlaneConfigRecord) -> None:
        raise NotImplementedError("ControlPlaneStartupConfigStore.append must be implemented")

    def records(self, *, section: str) -> list[ControlPlaneConfigRecord]:
        raise NotImplementedError("ControlPlaneStartupConfigStore.records must be implemented")

    def all_records(self) -> list[ControlPlaneConfigRecord]:
        raise NotImplementedError("ControlPlaneStartupConfigStore.all_records must be implemented")

    def clear(self) -> None:
        raise NotImplementedError("ControlPlaneStartupConfigStore.clear must be implemented")


@service(name="control_plane_startup_config_store")
@dataclass(slots=True)
class InMemoryControlPlaneStartupConfigStore(ControlPlaneStartupConfigStore):
    store: KVStore = inject.kv(ControlPlaneStartupConfigRegistry)

    def append(self, record: ControlPlaneConfigRecord) -> None:
        records = self._load_records()
        records.append(record)
        self.store.set(_CONFIG_RECORDS_KEY, records)

    def records(self, *, section: str) -> list[ControlPlaneConfigRecord]:
        if not isinstance(section, str) or not section:
            return []
        return [record for record in self._load_records() if record.section == section]

    def all_records(self) -> list[ControlPlaneConfigRecord]:
        return list(self._load_records())

    def clear(self) -> None:
        self.store.delete(_CONFIG_RECORDS_KEY)

    def _load_records(self) -> list[ControlPlaneConfigRecord]:
        existing = self.store.get(_CONFIG_RECORDS_KEY)
        if isinstance(existing, list):
            return [record for record in existing if isinstance(record, ControlPlaneConfigRecord)]
        return []


@runtime_checkable
class ControlPlaneConfigStreamAdapter(Protocol):
    def stream_records(self, runtime: dict[str, object]) -> list[ControlPlaneConfigRecord]:
        raise NotImplementedError("ControlPlaneConfigStreamAdapter.stream_records must be implemented")


@service(name="control_plane_config_stream_yaml_adapter")
@dataclass(slots=True)
class YamlControlPlaneConfigStreamAdapter(ControlPlaneConfigStreamAdapter):
    # Baseline YAML-backed section streaming. Runtime mapping is expected to be validated upstream.
    def stream_records(self, runtime: dict[str, object]) -> list[ControlPlaneConfigRecord]:
        if not isinstance(runtime, dict):
            raise ValueError("control plane config stream: runtime must be a mapping")
        records: list[ControlPlaneConfigRecord] = []

        system_payload = {k: v for k, v in runtime.items() if not (isinstance(k, str) and k.startswith("__"))}
        records.append(
            SystemRuntimeConfigRecord(
                source="runtime",
                section="system_runtime",
                record_id="system_runtime:0",
                payload=system_payload,
            )
        )

        if "observability" in runtime:
            observability = runtime.get("observability")
            if not isinstance(observability, dict):
                raise ValueError("control plane config stream: runtime.observability must be a mapping")
            records.append(
                ObservabilityConfigRecord(
                    source="runtime",
                    section="observability",
                    record_id="observability:0",
                    payload=dict(observability),
                )
            )

        platform = runtime.get("platform", {})
        if not isinstance(platform, dict):
            raise ValueError("control plane config stream: runtime.platform must be a mapping")
        process_groups = platform.get("process_groups", [])
        if not isinstance(process_groups, list):
            raise ValueError("control plane config stream: runtime.platform.process_groups must be a list")
        for index, group in enumerate(process_groups):
            if not isinstance(group, dict):
                raise ValueError(
                    f"control plane config stream: runtime.platform.process_groups[{index}] must be a mapping"
                )
            name = group.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"control plane config stream: runtime.platform.process_groups[{index}].name must be a non-empty string"
                )
            records.append(
                ExecutionGroupConfigRecord(
                    source="runtime",
                    section="execution_group",
                    record_id=f"execution_group:{index}:{name}",
                    payload=dict(group),
                )
            )

        if "nodes" in runtime:
            nodes = runtime.get("nodes")
            if not isinstance(nodes, dict):
                raise ValueError("control plane config stream: runtime.nodes must be a mapping")
            for node_name in sorted(nodes):
                payload = nodes[node_name]
                if not isinstance(node_name, str) or not node_name:
                    raise ValueError("control plane config stream: runtime.nodes key must be a non-empty string")
                if not isinstance(payload, dict):
                    raise ValueError(
                        f"control plane config stream: runtime.nodes['{node_name}'] must be a mapping"
                    )
                records.append(
                    NodeConfigRecord(
                        source="runtime",
                        section="node",
                        record_id=f"node:{node_name}",
                        payload=dict(payload),
                    )
                )

        return records


@adapter(
    name="control_plane_config_stream",
    kind="control_plane.config_stream",
    consumes=[],
    emits=[
        SystemRuntimeConfigRecord,
        ObservabilityConfigRecord,
        ExecutionGroupConfigRecord,
        NodeConfigRecord,
    ],
)
def control_plane_config_stream_adapter(settings: dict[str, object]) -> ControlPlaneConfigStreamAdapter:
    _ = settings
    return YamlControlPlaneConfigStreamAdapter()


@runtime_checkable
class ControlPlaneConfigStreamService(Protocol):
    def stream(self, runtime: dict[str, object]) -> list[object]:
        raise NotImplementedError("ControlPlaneConfigStreamService.stream must be implemented")


@service(name="control_plane_config_stream_service")
@dataclass(slots=True)
class DefaultControlPlaneConfigStreamService(ControlPlaneConfigStreamService):
    adapter: ControlPlaneConfigStreamAdapter = inject.service(ControlPlaneConfigStreamAdapter)
    store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)

    def stream(self, runtime: dict[str, object]) -> list[object]:
        adapter = self._adapter()
        records = adapter.stream_records(runtime)
        for record in records:
            self.store.append(record)
        return [
            *records,
            ControlPlaneConfigStreamCompletedEvent(
                runtime=runtime,
                record_count=len(records),
            ),
        ]

    def _adapter(self) -> ControlPlaneConfigStreamAdapter:
        adapter = self.adapter
        if not isinstance(adapter, ControlPlaneConfigStreamAdapter):
            raise ValueError("ControlPlaneConfigStreamAdapter binding is required for config stream")
        return adapter


__all__ = [
    "ControlPlaneConfigStreamAdapter",
    "ControlPlaneConfigStreamService",
    "ControlPlaneStartupConfigRegistry",
    "ControlPlaneStartupConfigStore",
    "DefaultControlPlaneConfigStreamService",
    "InMemoryControlPlaneStartupConfigStore",
    "YamlControlPlaneConfigStreamAdapter",
    "control_plane_config_stream_adapter",
]
