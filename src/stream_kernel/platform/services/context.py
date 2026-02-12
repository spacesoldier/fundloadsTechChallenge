from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import InMemoryKvStore, KVStore


@runtime_checkable
class ContextService(Protocol):
    # Service-level contract for context lifecycle and metadata views.
    def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
        raise NotImplementedError("ContextService.seed must be implemented")

    def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
        raise NotImplementedError("ContextService.metadata must be implemented")


@service(name="context_service")
@dataclass(slots=True)
class InMemoryKvContextService(ContextService):
    # Default context service backed by standard KV storage.
    store: KVStore = inject.kv(KVStore, qualifier="context")

    def seed(self, *, trace_id: str, payload: object, run_id: str, scenario_id: str) -> None:
        # Transport sequence (if present) is persisted for ordered sink delivery modes.
        seq = getattr(payload, "seq", None)
        seeded: dict[str, object] = {
            "__trace_id": trace_id,
            "__run_id": run_id,
            "__scenario_id": scenario_id,
        }
        if isinstance(seq, int):
            seeded["__seq"] = seq
        self.store.set(
            trace_id,
            seeded,
        )

    def metadata(self, trace_id: str | None, *, full: bool) -> dict[str, object]:
        if not trace_id:
            return {}
        ctx = self.store.get(trace_id)
        if ctx is None:
            return {}
        if not isinstance(ctx, dict):
            normalized: dict[str, object] = {"value": ctx}
            return normalized
        copied = dict(ctx)
        if full:
            return copied
        return {key: value for key, value in copied.items() if not key.startswith("__")}


@adapter(
    name="kv_store",
    kind="memory.kv_store",
    consumes=[],
    emits=[],
    binds=[("kv", KVStore)],
)
def kv_store_memory(settings: dict[str, object]) -> InMemoryKvStore:
    # Default platform KV adapter used by services requiring key-value persistence.
    _ = settings
    return InMemoryKvStore()
