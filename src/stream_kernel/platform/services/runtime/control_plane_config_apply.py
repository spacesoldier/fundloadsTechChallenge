from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.integration.kv_store import KVStore
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneConfigRecord,
    NodeConfigRecord,
    ObservabilityConfigRecord,
    SystemRuntimeConfigRecord,
)

_APPLIED_CONFIG_RECORDS_KEY = "control_plane.config_apply.records"


class ControlPlaneAppliedConfigRegistry(KVStore):
    # KV marker contract for applied startup config state.
    pass


@runtime_checkable
class ControlPlaneAppliedConfigStore(Protocol):
    def append(self, record: ControlPlaneConfigRecord) -> None:
        raise NotImplementedError("ControlPlaneAppliedConfigStore.append must be implemented")

    def records(self, *, section: str) -> list[ControlPlaneConfigRecord]:
        raise NotImplementedError("ControlPlaneAppliedConfigStore.records must be implemented")

    def count(self, *, section: str) -> int:
        raise NotImplementedError("ControlPlaneAppliedConfigStore.count must be implemented")

    def all_records(self) -> list[ControlPlaneConfigRecord]:
        raise NotImplementedError("ControlPlaneAppliedConfigStore.all_records must be implemented")

    def clear(self) -> None:
        raise NotImplementedError("ControlPlaneAppliedConfigStore.clear must be implemented")


@service(name="control_plane_applied_config_store")
@dataclass(slots=True)
class InMemoryControlPlaneAppliedConfigStore(ControlPlaneAppliedConfigStore):
    store: KVStore = inject.kv(ControlPlaneAppliedConfigRegistry)

    def append(self, record: ControlPlaneConfigRecord) -> None:
        records = self._load_records()
        records.append(record)
        self.store.set(_APPLIED_CONFIG_RECORDS_KEY, records)

    def records(self, *, section: str) -> list[ControlPlaneConfigRecord]:
        if not isinstance(section, str) or not section:
            return []
        return [record for record in self._load_records() if record.section == section]

    def count(self, *, section: str) -> int:
        return len(self.records(section=section))

    def all_records(self) -> list[ControlPlaneConfigRecord]:
        return list(self._load_records())

    def clear(self) -> None:
        self.store.delete(_APPLIED_CONFIG_RECORDS_KEY)

    def _load_records(self) -> list[ControlPlaneConfigRecord]:
        existing = self.store.get(_APPLIED_CONFIG_RECORDS_KEY)
        if isinstance(existing, list):
            return [record for record in existing if isinstance(record, ControlPlaneConfigRecord)]
        return []


@runtime_checkable
class ControlPlaneSystemConfigApplyService(Protocol):
    def apply(self, record: SystemRuntimeConfigRecord) -> bool:
        raise NotImplementedError("ControlPlaneSystemConfigApplyService.apply must be implemented")


@runtime_checkable
class ControlPlaneObservabilityConfigApplyService(Protocol):
    def apply(self, record: ObservabilityConfigRecord) -> bool:
        raise NotImplementedError("ControlPlaneObservabilityConfigApplyService.apply must be implemented")


@runtime_checkable
class ControlPlaneNodeConfigApplyService(Protocol):
    def apply(self, record: NodeConfigRecord) -> bool:
        raise NotImplementedError("ControlPlaneNodeConfigApplyService.apply must be implemented")


@service(name="control_plane_system_config_apply_service")
@dataclass(slots=True)
class DefaultControlPlaneSystemConfigApplyService(ControlPlaneSystemConfigApplyService):
    store: ControlPlaneAppliedConfigStore = inject.service(ControlPlaneAppliedConfigStore)

    def apply(self, record: SystemRuntimeConfigRecord) -> bool:
        if not isinstance(record, SystemRuntimeConfigRecord):
            return False
        self.store.append(record)
        return True


@service(name="control_plane_observability_config_apply_service")
@dataclass(slots=True)
class DefaultControlPlaneObservabilityConfigApplyService(ControlPlaneObservabilityConfigApplyService):
    store: ControlPlaneAppliedConfigStore = inject.service(ControlPlaneAppliedConfigStore)

    def apply(self, record: ObservabilityConfigRecord) -> bool:
        if not isinstance(record, ObservabilityConfigRecord):
            return False
        self.store.append(record)
        return True


@service(name="control_plane_node_config_apply_service")
@dataclass(slots=True)
class DefaultControlPlaneNodeConfigApplyService(ControlPlaneNodeConfigApplyService):
    store: ControlPlaneAppliedConfigStore = inject.service(ControlPlaneAppliedConfigStore)

    def apply(self, record: NodeConfigRecord) -> bool:
        if not isinstance(record, NodeConfigRecord):
            return False
        self.store.append(record)
        return True


@dataclass(frozen=True, slots=True)
class ControlPlaneConfigApplyProgress:
    runtime: dict[str, object] | None
    expected_counts: dict[str, int]
    applied_counts: dict[str, int]
    stream_completed: bool
    completed: bool


@runtime_checkable
class ControlPlaneConfigApplyTrackerService(Protocol):
    def mark_stream_completed(self, *, runtime: dict[str, object], expected_counts: dict[str, int]) -> bool:
        raise NotImplementedError(
            "ControlPlaneConfigApplyTrackerService.mark_stream_completed must be implemented"
        )

    def mark_section_applied(self, *, section: str) -> bool:
        raise NotImplementedError(
            "ControlPlaneConfigApplyTrackerService.mark_section_applied must be implemented"
        )

    def progress(self) -> ControlPlaneConfigApplyProgress:
        raise NotImplementedError("ControlPlaneConfigApplyTrackerService.progress must be implemented")

    def reset(self) -> None:
        raise NotImplementedError("ControlPlaneConfigApplyTrackerService.reset must be implemented")


@service(name="control_plane_config_apply_tracker_service")
@dataclass(slots=True)
class InMemoryControlPlaneConfigApplyTrackerService(ControlPlaneConfigApplyTrackerService):
    _runtime: dict[str, object] | None = None
    _expected_counts: dict[str, int] = field(
        default_factory=lambda: {"system_runtime": 0, "observability": 0, "node": 0}
    )
    _applied_counts: dict[str, int] = field(
        default_factory=lambda: {"system_runtime": 0, "observability": 0, "node": 0}
    )
    _stream_completed: bool = False
    _completed: bool = False

    def mark_stream_completed(self, *, runtime: dict[str, object], expected_counts: dict[str, int]) -> bool:
        self._runtime = dict(runtime) if isinstance(runtime, dict) else {}
        self._expected_counts = _normalize_section_counts(expected_counts)
        self._stream_completed = True
        return self._maybe_complete()

    def mark_section_applied(self, *, section: str) -> bool:
        if not isinstance(section, str) or not section:
            return False
        if section not in self._applied_counts:
            self._applied_counts[section] = 0
        self._applied_counts[section] = int(self._applied_counts.get(section, 0)) + 1
        return self._maybe_complete()

    def progress(self) -> ControlPlaneConfigApplyProgress:
        return ControlPlaneConfigApplyProgress(
            runtime=dict(self._runtime) if isinstance(self._runtime, dict) else None,
            expected_counts=dict(self._expected_counts),
            applied_counts=dict(self._applied_counts),
            stream_completed=self._stream_completed,
            completed=self._completed,
        )

    def reset(self) -> None:
        self._runtime = None
        self._expected_counts = {"system_runtime": 0, "observability": 0, "node": 0}
        self._applied_counts = {"system_runtime": 0, "observability": 0, "node": 0}
        self._stream_completed = False
        self._completed = False

    def _maybe_complete(self) -> bool:
        if self._completed or not self._stream_completed:
            return False
        for section, expected_count in self._expected_counts.items():
            if int(self._applied_counts.get(section, 0)) < int(expected_count):
                return False
        self._completed = True
        return True


def resolve_expected_config_apply_counts(
    config_store: ControlPlaneStartupConfigStore | object,
) -> dict[str, int]:
    records_method = getattr(config_store, "records", None)
    if not callable(records_method):
        return {"system_runtime": 0, "observability": 0, "node": 0}
    counts: dict[str, int] = {"system_runtime": 0, "observability": 0, "node": 0}
    for section in ("system_runtime", "observability", "node"):
        try:
            section_records = list(records_method(section=section))
        except Exception:
            section_records = []
        counts[section] = len([item for item in section_records if isinstance(item, ControlPlaneConfigRecord)])
    return counts


def _normalize_section_counts(counts: dict[str, int]) -> dict[str, int]:
    normalized = {"system_runtime": 0, "observability": 0, "node": 0}
    if not isinstance(counts, dict):
        return normalized
    for section in normalized:
        value = counts.get(section, 0)
        normalized[section] = value if isinstance(value, int) and value >= 0 else 0
    return normalized


__all__ = [
    "ControlPlaneAppliedConfigRegistry",
    "ControlPlaneAppliedConfigStore",
    "ControlPlaneSystemConfigApplyService",
    "ControlPlaneObservabilityConfigApplyService",
    "ControlPlaneNodeConfigApplyService",
    "ControlPlaneConfigApplyProgress",
    "ControlPlaneConfigApplyTrackerService",
    "DefaultControlPlaneSystemConfigApplyService",
    "DefaultControlPlaneObservabilityConfigApplyService",
    "DefaultControlPlaneNodeConfigApplyService",
    "InMemoryControlPlaneAppliedConfigStore",
    "InMemoryControlPlaneConfigApplyTrackerService",
    "resolve_expected_config_apply_counts",
]
