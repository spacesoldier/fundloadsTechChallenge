from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.adapters.contracts import AdapterMeta, get_adapter_meta
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneDiscoveryAdapter,
    DefaultControlPlaneDiscoveryAdapter,
    DefaultControlPlaneBootstrapperService,
    control_plane_discovery_adapter,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryItemEvent,
)


def test_control_plane_discovery_adapter_factory_exposes_adapter_metadata() -> None:
    meta = get_adapter_meta(control_plane_discovery_adapter)
    assert isinstance(meta, AdapterMeta)
    assert meta.name == "control_plane_discovery"
    assert meta.kind == "control_plane.discovery"
    assert meta.execution_mode == "sync"


def test_control_plane_discovery_adapter_factory_returns_discovery_port_impl() -> None:
    resolved = control_plane_discovery_adapter({})
    assert isinstance(resolved, ControlPlaneDiscoveryAdapter)
    assert resolved.discover_all({"platform": {"process_groups": []}}) == []
    assert resolved.discover_subset(runtime={}, node_names=["a", "b"]) == []


def test_default_bootstrapper_service_uses_injected_discovery_adapter_contract() -> None:
    @dataclass(slots=True)
    class _Adapter(ControlPlaneDiscoveryAdapter):
        items: list[ControlPlaneDiscoveryItemEvent]
        subset_items: list[ControlPlaneDiscoveryItemEvent]

        def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
            _ = runtime
            return list(self.items)

        def discover_subset(
            self,
            *,
            runtime: dict[str, object],
            node_names: list[str],
        ) -> list[ControlPlaneDiscoveryItemEvent]:
            _ = (runtime, node_names)
            return list(self.subset_items)

    adapter = _Adapter(
        items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "n1"})],
        subset_items=[ControlPlaneDiscoveryItemEvent(item_kind="node", payload={"name": "n2"})],
    )
    service = DefaultControlPlaneBootstrapperService(adapter=adapter)

    all_items = service.discover_all({"platform": {"process_groups": []}})
    subset_items = service.discover_subset(runtime={}, node_names=["n2"])

    assert all_items == adapter.items
    assert subset_items == adapter.subset_items


def test_default_discovery_adapter_reads_items_from_discovery_stream() -> None:
    @dataclass(slots=True)
    class _Stream:
        def start(self, event: object) -> list[object]:
            _ = event
            return [
                ControlPlaneDiscoveryBatchRequestedEvent(
                    session_id="s-1",
                    source_scope="platform",
                    cursor=0,
                    limit=128,
                )
            ]

        def request_batch(self, event: object) -> list[object]:
            if not isinstance(event, ControlPlaneDiscoveryBatchRequestedEvent):
                return []
            return [
                ControlPlaneDiscoveryBatchReadyEvent(
                    session_id=event.session_id,
                    source_scope=event.source_scope,
                    cursor=event.cursor,
                    entities=(
                        ControlPlaneDiscoveryEntityRecord(
                            entity_kind="node",
                            entity_id="node:format_output",
                            source_scope="project",
                            module="fund_load.usecases.steps.format_output",
                            qualname="format_output",
                            meta={"name": "format_output"},
                        ),
                    ),
                )
            ]

    adapter = DefaultControlPlaneDiscoveryAdapter(stream=_Stream())

    all_items = adapter.discover_all({"platform": {"process_groups": []}})
    subset = adapter.discover_subset(runtime={}, node_names=["format_output"])

    assert len(all_items) == 1
    assert all_items[0].item_kind == "node"
    assert all_items[0].payload.get("name") == "format_output"
    assert len(subset) == 1
    assert subset[0].payload.get("name") == "format_output"
