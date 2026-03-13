from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConsumerBindingRecord,
)

_BINDINGS_KEY = "control_plane.startup_consumer_bindings.records"
_EMITTED_KEY = "control_plane.startup_consumer_bindings.emitted"


class ControlPlaneStartupConsumerBindingsStore(KVStore):
    # KV marker for startup-time staged consumer bindings.
    pass


@runtime_checkable
class ControlPlaneStartupConsumerBindingsService(Protocol):
    def seed_bindings(
        self,
        bindings: tuple[ControlPlaneConsumerBindingRecord, ...] | list[ControlPlaneConsumerBindingRecord],
    ) -> None:
        raise NotImplementedError

    def collect_once(self) -> tuple[ControlPlaneConsumerBindingRecord, ...]:
        raise NotImplementedError


@service(name="control_plane_startup_consumer_bindings_service")
@dataclass(slots=True)
class InMemoryControlPlaneStartupConsumerBindingsService(ControlPlaneStartupConsumerBindingsService):
    store: KVStore = inject.kv(ControlPlaneStartupConsumerBindingsStore)

    def seed_bindings(
        self,
        bindings: tuple[ControlPlaneConsumerBindingRecord, ...] | list[ControlPlaneConsumerBindingRecord],
    ) -> None:
        normalized = tuple(
            binding
            for binding in bindings
            if isinstance(binding, ControlPlaneConsumerBindingRecord)
            and isinstance(binding.token, type)
            and bool(binding.node_names)
        )
        self.store.set(_BINDINGS_KEY, normalized)
        self.store.set(_EMITTED_KEY, False)

    def collect_once(self) -> tuple[ControlPlaneConsumerBindingRecord, ...]:
        emitted = self.store.get(_EMITTED_KEY)
        if emitted is True:
            return ()
        raw = self.store.get(_BINDINGS_KEY)
        records = (
            tuple(
                binding
                for binding in raw
                if isinstance(binding, ControlPlaneConsumerBindingRecord)
            )
            if isinstance(raw, tuple | list)
            else ()
        )
        self.store.set(_EMITTED_KEY, True)
        return records


__all__ = [
    "ControlPlaneStartupConsumerBindingsStore",
    "ControlPlaneStartupConsumerBindingsService",
    "InMemoryControlPlaneStartupConsumerBindingsService",
]

