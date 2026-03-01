from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryBatchReadyEvent,
    ControlPlaneDiscoveryBatchRequestedEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneDiscoveryStartRequestedEvent,
)
from stream_kernel.platform.services.runtime.control_plane_discovery_stream import (
    ControlPlaneDiscoveryStreamService,
)


@runtime_checkable
class ControlPlaneDiscoveryAdapter(Protocol):
    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        raise NotImplementedError("ControlPlaneDiscoveryAdapter.discover_all must be implemented")

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        raise NotImplementedError("ControlPlaneDiscoveryAdapter.discover_subset must be implemented")


@runtime_checkable
class ControlPlaneBootstrapperService(Protocol):
    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        raise NotImplementedError("ControlPlaneBootstrapperService.discover_all must be implemented")

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        raise NotImplementedError("ControlPlaneBootstrapperService.discover_subset must be implemented")


@service(name="control_plane_discovery_adapter")
@dataclass(slots=True)
class DefaultControlPlaneDiscoveryAdapter(ControlPlaneDiscoveryAdapter):
    stream: object | None = inject.service(ControlPlaneDiscoveryStreamService)

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        stream = self._stream()
        if stream is None:
            return []
        start = getattr(stream, "start", None)
        request_batch = getattr(stream, "request_batch", None)
        if not callable(start) or not callable(request_batch):
            return []
        queue: list[object] = list(start(ControlPlaneDiscoveryStartRequestedEvent(runtime=dict(runtime))))
        items: list[ControlPlaneDiscoveryItemEvent] = []
        processed_events = 0
        while queue:
            event = queue.pop(0)
            processed_events += 1
            if processed_events > 100_000:
                break
            if isinstance(event, ControlPlaneDiscoveryBatchReadyEvent):
                for entity in event.entities:
                    item = self._to_item_event(entity)
                    if item is not None:
                        items.append(item)
                continue
            if isinstance(event, ControlPlaneDiscoveryBatchRequestedEvent):
                queue.extend(request_batch(event))
        return items

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        required = {name for name in node_names if isinstance(name, str) and name}
        if not required:
            return []
        items = self.discover_all(runtime)
        selected: list[ControlPlaneDiscoveryItemEvent] = []
        for item in items:
            if item.item_kind != "node":
                continue
            name = item.payload.get("name")
            if isinstance(name, str) and name in required:
                selected.append(item)
        return selected

    def _stream(self) -> ControlPlaneDiscoveryStreamService | None:
        candidate = self.stream
        if isinstance(candidate, ControlPlaneDiscoveryStreamService):
            return candidate
        if callable(getattr(candidate, "start", None)) and callable(getattr(candidate, "request_batch", None)):
            return candidate  # type: ignore[return-value]
        return None

    @staticmethod
    def _to_item_event(entity: ControlPlaneDiscoveryEntityRecord) -> ControlPlaneDiscoveryItemEvent | None:
        if not isinstance(entity, ControlPlaneDiscoveryEntityRecord):
            return None
        payload = dict(entity.meta)
        payload.setdefault("id", entity.entity_id)
        payload.setdefault("source_scope", entity.source_scope)
        payload.setdefault("module", entity.module)
        payload.setdefault("qualname", entity.qualname)
        return ControlPlaneDiscoveryItemEvent(
            item_kind=entity.entity_kind,
            payload=payload,
        )


@adapter(
    name="control_plane_discovery",
    kind="control_plane.discovery",
    consumes=[],
    emits=[ControlPlaneDiscoveryItemEvent],
)
def control_plane_discovery_adapter(settings: dict[str, object]) -> ControlPlaneDiscoveryAdapter:
    _ = settings
    return DefaultControlPlaneDiscoveryAdapter()


@service(name="control_plane_bootstrapper_service")
@dataclass(slots=True)
class DefaultControlPlaneBootstrapperService(ControlPlaneBootstrapperService):
    adapter: ControlPlaneDiscoveryAdapter = inject.service(ControlPlaneDiscoveryAdapter)

    def discover_all(self, runtime: dict[str, object]) -> list[ControlPlaneDiscoveryItemEvent]:
        return self._adapter().discover_all(runtime)

    def discover_subset(
        self,
        *,
        runtime: dict[str, object],
        node_names: list[str],
    ) -> list[ControlPlaneDiscoveryItemEvent]:
        return self._adapter().discover_subset(runtime=runtime, node_names=node_names)

    def _adapter(self) -> ControlPlaneDiscoveryAdapter:
        adapter = self.adapter
        if not isinstance(adapter, ControlPlaneDiscoveryAdapter):
            raise ValueError("ControlPlaneDiscoveryAdapter binding is required for bootstrapper")
        return adapter


__all__ = [
    "ControlPlaneBootstrapperService",
    "ControlPlaneDiscoveryAdapter",
    "DefaultControlPlaneDiscoveryAdapter",
    "DefaultControlPlaneBootstrapperService",
    "control_plane_discovery_adapter",
]
