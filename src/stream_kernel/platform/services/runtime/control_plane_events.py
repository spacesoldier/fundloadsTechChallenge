from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ControlPlaneInitEvent:
    runtime: dict[str, object]
    discovery: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ControlPlaneRootPulse:
    runtime: dict[str, object]


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafPulse:
    runtime: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafHelloEvent:
    target_group: str
    worker_id: str
    pid: int | None = None
    runner_profile: str | None = None
    emitted_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        if not isinstance(self.target_group, str) or not self.target_group:
            raise ValueError("ControlPlaneLeafHelloEvent.target_group must be a non-empty string")
        if not isinstance(self.worker_id, str) or not self.worker_id:
            raise ValueError("ControlPlaneLeafHelloEvent.worker_id must be a non-empty string")
        if self.pid is not None and (not isinstance(self.pid, int) or self.pid <= 0):
            raise ValueError("ControlPlaneLeafHelloEvent.pid must be a positive int when provided")
        if self.runner_profile is not None and (
            not isinstance(self.runner_profile, str) or not self.runner_profile
        ):
            raise ValueError(
                "ControlPlaneLeafHelloEvent.runner_profile must be a non-empty string when provided"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafDiscoveryRequestEvent:
    target_group: str
    worker_id: str
    request_id: str
    required_nodes: tuple[str, ...] = field(default_factory=tuple)
    include_relationships: bool = False
    protocol_revision: int = 1
    issued_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(
            self.target_group,
            "ControlPlaneLeafDiscoveryRequestEvent.target_group",
        )
        _require_non_empty_str(
            self.worker_id,
            "ControlPlaneLeafDiscoveryRequestEvent.worker_id",
        )
        _require_non_empty_str(
            self.request_id,
            "ControlPlaneLeafDiscoveryRequestEvent.request_id",
        )
        if any(not isinstance(name, str) or not name for name in self.required_nodes):
            raise ValueError(
                "ControlPlaneLeafDiscoveryRequestEvent.required_nodes must contain non-empty strings"
            )
        if not isinstance(self.include_relationships, bool):
            raise ValueError(
                "ControlPlaneLeafDiscoveryRequestEvent.include_relationships must be a boolean"
            )
        if not isinstance(self.protocol_revision, int) or self.protocol_revision <= 0:
            raise ValueError(
                "ControlPlaneLeafDiscoveryRequestEvent.protocol_revision must be an integer > 0"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafDiscoverySnapshotEvent:
    target_group: str
    worker_id: str
    request_id: str
    required_nodes: tuple[str, ...] = field(default_factory=tuple)
    snapshot_records: tuple["ControlPlaneDiscoveryEntityRecord", ...] = field(default_factory=tuple)
    protocol_revision: int = 3
    issued_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(
            self.target_group,
            "ControlPlaneLeafDiscoverySnapshotEvent.target_group",
        )
        _require_non_empty_str(
            self.worker_id,
            "ControlPlaneLeafDiscoverySnapshotEvent.worker_id",
        )
        _require_non_empty_str(
            self.request_id,
            "ControlPlaneLeafDiscoverySnapshotEvent.request_id",
        )
        if any(not isinstance(name, str) or not name for name in self.required_nodes):
            raise ValueError(
                "ControlPlaneLeafDiscoverySnapshotEvent.required_nodes must contain non-empty strings"
            )
        if any(not isinstance(item, ControlPlaneDiscoveryEntityRecord) for item in self.snapshot_records):
            raise ValueError(
                "ControlPlaneLeafDiscoverySnapshotEvent.snapshot_records must contain ControlPlaneDiscoveryEntityRecord"
            )
        if not isinstance(self.protocol_revision, int) or self.protocol_revision <= 0:
            raise ValueError(
                "ControlPlaneLeafDiscoverySnapshotEvent.protocol_revision must be an integer > 0"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafDiscoveryAckEvent:
    target_group: str
    worker_id: str
    request_id: str
    status: str
    discovered_nodes: tuple[str, ...] = field(default_factory=tuple)
    missing_nodes: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None
    emitted_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(
            self.target_group,
            "ControlPlaneLeafDiscoveryAckEvent.target_group",
        )
        _require_non_empty_str(
            self.worker_id,
            "ControlPlaneLeafDiscoveryAckEvent.worker_id",
        )
        _require_non_empty_str(
            self.request_id,
            "ControlPlaneLeafDiscoveryAckEvent.request_id",
        )
        _require_non_empty_str(
            self.status,
            "ControlPlaneLeafDiscoveryAckEvent.status",
        )
        if self.status not in {"accepted", "rejected"}:
            raise ValueError(
                "ControlPlaneLeafDiscoveryAckEvent.status must be one of: accepted, rejected"
            )
        if any(not isinstance(name, str) or not name for name in self.discovered_nodes):
            raise ValueError(
                "ControlPlaneLeafDiscoveryAckEvent.discovered_nodes must contain non-empty strings"
            )
        if any(not isinstance(name, str) or not name for name in self.missing_nodes):
            raise ValueError(
                "ControlPlaneLeafDiscoveryAckEvent.missing_nodes must contain non-empty strings"
            )
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError(
                "ControlPlaneLeafDiscoveryAckEvent.error must be a non-empty string when provided"
            )
        if self.status == "accepted" and self.error is not None:
            raise ValueError(
                "ControlPlaneLeafDiscoveryAckEvent.error must be None when status=accepted"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafConfigCardEvent:
    target_group: str
    worker_id: str
    config_id: str
    run_id: str
    scenario_id: str
    group_name: str
    nodes: tuple[str, ...] = field(default_factory=tuple)
    runner_profile: str | None = None
    worker_slot: int | None = None
    config_revision: int = 1
    issued_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafConfigCardEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafConfigCardEvent.worker_id")
        _require_non_empty_str(self.config_id, "ControlPlaneLeafConfigCardEvent.config_id")
        _require_non_empty_str(self.run_id, "ControlPlaneLeafConfigCardEvent.run_id")
        _require_non_empty_str(self.scenario_id, "ControlPlaneLeafConfigCardEvent.scenario_id")
        _require_non_empty_str(self.group_name, "ControlPlaneLeafConfigCardEvent.group_name")
        if any(not isinstance(name, str) or not name for name in self.nodes):
            raise ValueError("ControlPlaneLeafConfigCardEvent.nodes must contain non-empty strings")
        if self.runner_profile is not None and (
            not isinstance(self.runner_profile, str) or not self.runner_profile
        ):
            raise ValueError(
                "ControlPlaneLeafConfigCardEvent.runner_profile must be a non-empty string when provided"
            )
        if self.worker_slot is not None and (not isinstance(self.worker_slot, int) or self.worker_slot < 0):
            raise ValueError(
                "ControlPlaneLeafConfigCardEvent.worker_slot must be >= 0 when provided"
            )
        if not isinstance(self.config_revision, int) or self.config_revision <= 0:
            raise ValueError("ControlPlaneLeafConfigCardEvent.config_revision must be an integer > 0")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafConfigAckEvent:
    target_group: str
    worker_id: str
    config_id: str
    status: str
    resolved_nodes: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None
    emitted_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafConfigAckEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafConfigAckEvent.worker_id")
        _require_non_empty_str(self.config_id, "ControlPlaneLeafConfigAckEvent.config_id")
        _require_non_empty_str(self.status, "ControlPlaneLeafConfigAckEvent.status")
        if any(not isinstance(name, str) or not name for name in self.resolved_nodes):
            raise ValueError("ControlPlaneLeafConfigAckEvent.resolved_nodes must contain non-empty strings")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError("ControlPlaneLeafConfigAckEvent.error must be a non-empty string when provided")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafStopCommand:
    target_group: str
    worker_id: str
    command_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafStopCommand.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafStopCommand.worker_id")
        _require_non_empty_str(self.command_id, "ControlPlaneLeafStopCommand.command_id")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason):
            raise ValueError("ControlPlaneLeafStopCommand.reason must be a non-empty string when provided")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafStopAckEvent:
    target_group: str
    worker_id: str
    command_id: str
    status: str = "accepted"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafStopAckEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafStopAckEvent.worker_id")
        _require_non_empty_str(self.command_id, "ControlPlaneLeafStopAckEvent.command_id")
        _require_non_empty_str(self.status, "ControlPlaneLeafStopAckEvent.status")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafBoundaryExecuteCommand:
    target_group: str
    worker_id: str
    request_id: str
    inputs: tuple[object, ...] = field(default_factory=tuple)
    finalize: bool = True

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafBoundaryExecuteCommand.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafBoundaryExecuteCommand.worker_id")
        _require_non_empty_str(self.request_id, "ControlPlaneLeafBoundaryExecuteCommand.request_id")
        if not isinstance(self.finalize, bool):
            raise ValueError("ControlPlaneLeafBoundaryExecuteCommand.finalize must be a boolean")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafBoundaryResultEvent:
    target_group: str
    worker_id: str
    request_id: str
    status: str
    outputs: tuple[object, ...] = field(default_factory=tuple)
    error: str | None = None
    tombstone_input: bool = False
    tombstone_output: bool = False

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafBoundaryResultEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafBoundaryResultEvent.worker_id")
        _require_non_empty_str(self.request_id, "ControlPlaneLeafBoundaryResultEvent.request_id")
        _require_non_empty_str(self.status, "ControlPlaneLeafBoundaryResultEvent.status")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError("ControlPlaneLeafBoundaryResultEvent.error must be a non-empty string when provided")
        if not isinstance(self.tombstone_input, bool):
            raise ValueError("ControlPlaneLeafBoundaryResultEvent.tombstone_input must be a boolean")
        if not isinstance(self.tombstone_output, bool):
            raise ValueError("ControlPlaneLeafBoundaryResultEvent.tombstone_output must be a boolean")


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafDrainReadyEvent:
    target_group: str
    worker_id: str
    request_id: str
    tombstone_output: bool = False
    emitted_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneLeafDrainReadyEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneLeafDrainReadyEvent.worker_id")
        _require_non_empty_str(self.request_id, "ControlPlaneLeafDrainReadyEvent.request_id")
        if not isinstance(self.tombstone_output, bool):
            raise ValueError("ControlPlaneLeafDrainReadyEvent.tombstone_output must be a boolean")


@dataclass(frozen=True, slots=True)
class ControlPlaneShutdownReadyEvent:
    expected_groups: tuple[str, ...] = field(default_factory=tuple)
    ready_groups: tuple[str, ...] = field(default_factory=tuple)
    tombstone_groups: tuple[str, ...] = field(default_factory=tuple)
    emitted_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        if any(not isinstance(name, str) or not name for name in self.expected_groups):
            raise ValueError("ControlPlaneShutdownReadyEvent.expected_groups must contain non-empty strings")
        if any(not isinstance(name, str) or not name for name in self.ready_groups):
            raise ValueError("ControlPlaneShutdownReadyEvent.ready_groups must contain non-empty strings")
        if any(not isinstance(name, str) or not name for name in self.tombstone_groups):
            raise ValueError("ControlPlaneShutdownReadyEvent.tombstone_groups must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryItemEvent:
    item_kind: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.item_kind, str) or not self.item_kind:
            raise ValueError("ControlPlaneDiscoveryItemEvent.item_kind must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("ControlPlaneDiscoveryItemEvent.payload must be a mapping")


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryEntityRecord:
    entity_kind: str
    entity_id: str
    source_scope: str
    module: str
    qualname: str
    meta: dict[str, object]

    def __post_init__(self) -> None:
        if self.entity_kind not in {"node", "service", "adapter"}:
            raise ValueError(
                "ControlPlaneDiscoveryEntityRecord.entity_kind must be one of: node, service, adapter"
            )
        _require_non_empty_str(self.entity_id, "ControlPlaneDiscoveryEntityRecord.entity_id")
        if self.source_scope not in {"platform", "project"}:
            raise ValueError(
                "ControlPlaneDiscoveryEntityRecord.source_scope must be one of: platform, project"
            )
        _require_non_empty_str(self.module, "ControlPlaneDiscoveryEntityRecord.module")
        _require_non_empty_str(self.qualname, "ControlPlaneDiscoveryEntityRecord.qualname")
        if not isinstance(self.meta, dict):
            raise ValueError("ControlPlaneDiscoveryEntityRecord.meta must be a mapping")


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryStartRequestedEvent:
    runtime: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, dict):
            raise ValueError("ControlPlaneDiscoveryStartRequestedEvent.runtime must be a mapping")


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryBatchRequestedEvent:
    session_id: str
    source_scope: str
    cursor: int = 0
    limit: int = 128

    def __post_init__(self) -> None:
        _require_non_empty_str(self.session_id, "ControlPlaneDiscoveryBatchRequestedEvent.session_id")
        if self.source_scope not in {"platform", "project"}:
            raise ValueError(
                "ControlPlaneDiscoveryBatchRequestedEvent.source_scope must be one of: platform, project"
            )
        if not isinstance(self.cursor, int) or self.cursor < 0:
            raise ValueError("ControlPlaneDiscoveryBatchRequestedEvent.cursor must be an integer >= 0")
        if not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("ControlPlaneDiscoveryBatchRequestedEvent.limit must be an integer > 0")


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryBatchReadyEvent:
    session_id: str
    source_scope: str
    cursor: int
    entities: tuple[ControlPlaneDiscoveryEntityRecord, ...] = field(default_factory=tuple)
    has_more: bool = False
    next_cursor: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.session_id, "ControlPlaneDiscoveryBatchReadyEvent.session_id")
        if self.source_scope not in {"platform", "project"}:
            raise ValueError(
                "ControlPlaneDiscoveryBatchReadyEvent.source_scope must be one of: platform, project"
            )
        if not isinstance(self.cursor, int) or self.cursor < 0:
            raise ValueError("ControlPlaneDiscoveryBatchReadyEvent.cursor must be an integer >= 0")
        if any(not isinstance(item, ControlPlaneDiscoveryEntityRecord) for item in self.entities):
            raise ValueError(
                "ControlPlaneDiscoveryBatchReadyEvent.entities must contain ControlPlaneDiscoveryEntityRecord items"
            )
        if not isinstance(self.has_more, bool):
            raise ValueError("ControlPlaneDiscoveryBatchReadyEvent.has_more must be a boolean")
        if self.has_more:
            if not isinstance(self.next_cursor, int) or self.next_cursor <= self.cursor:
                raise ValueError(
                    "ControlPlaneDiscoveryBatchReadyEvent.next_cursor must be an integer > cursor when has_more=True"
                )
        else:
            if self.next_cursor is not None:
                raise ValueError(
                    "ControlPlaneDiscoveryBatchReadyEvent.next_cursor must be None when has_more=False"
                )


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoverySourceCompletedEvent:
    session_id: str
    source_scope: str
    total_emitted: int

    def __post_init__(self) -> None:
        _require_non_empty_str(self.session_id, "ControlPlaneDiscoverySourceCompletedEvent.session_id")
        if self.source_scope not in {"platform", "project"}:
            raise ValueError(
                "ControlPlaneDiscoverySourceCompletedEvent.source_scope must be one of: platform, project"
            )
        if not isinstance(self.total_emitted, int) or self.total_emitted < 0:
            raise ValueError(
                "ControlPlaneDiscoverySourceCompletedEvent.total_emitted must be an integer >= 0"
            )


def discovery_entity_sort_key(entity: ControlPlaneDiscoveryEntityRecord) -> tuple[int, str, str]:
    scope_order = 0 if entity.source_scope == "platform" else 1
    return (scope_order, entity.module, entity.qualname)


@dataclass(frozen=True, slots=True)
class ControlPlaneDiscoveryCompletedEvent:
    runtime: dict[str, object]


@dataclass(frozen=True, slots=True)
class ControlPlaneDagAssemblyRequestedEvent:
    runtime: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, dict):
            raise ValueError("ControlPlaneDagAssemblyRequestedEvent.runtime must be a mapping")


@dataclass(frozen=True, slots=True)
class ControlPlaneConfigRecord:
    source: str
    section: str
    record_id: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        _require_non_empty_str(self.source, f"{self.__class__.__name__}.source")
        _require_non_empty_str(self.section, f"{self.__class__.__name__}.section")
        _require_non_empty_str(self.record_id, f"{self.__class__.__name__}.record_id")
        if not isinstance(self.payload, dict):
            raise ValueError(f"{self.__class__.__name__}.payload must be a mapping")


@dataclass(frozen=True, slots=True)
class SystemRuntimeConfigRecord(ControlPlaneConfigRecord):
    pass


@dataclass(frozen=True, slots=True)
class ObservabilityConfigRecord(ControlPlaneConfigRecord):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionGroupConfigRecord(ControlPlaneConfigRecord):
    pass


@dataclass(frozen=True, slots=True)
class NodeConfigRecord(ControlPlaneConfigRecord):
    pass


@dataclass(frozen=True, slots=True)
class ControlPlaneConfigStreamCompletedEvent:
    runtime: dict[str, object]
    record_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, dict):
            raise ValueError("ControlPlaneConfigStreamCompletedEvent.runtime must be a mapping")
        if not isinstance(self.record_count, int) or self.record_count < 0:
            raise ValueError(
                "ControlPlaneConfigStreamCompletedEvent.record_count must be an integer >= 0"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneSystemConfigAppliedEvent:
    record_id: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.record_id, "ControlPlaneSystemConfigAppliedEvent.record_id")


@dataclass(frozen=True, slots=True)
class ControlPlaneObservabilityConfigAppliedEvent:
    record_id: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.record_id, "ControlPlaneObservabilityConfigAppliedEvent.record_id")


@dataclass(frozen=True, slots=True)
class ControlPlaneNodeConfigAppliedEvent:
    record_id: str
    node_name: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.record_id, "ControlPlaneNodeConfigAppliedEvent.record_id")
        if self.node_name is not None and (not isinstance(self.node_name, str) or not self.node_name):
            raise ValueError("ControlPlaneNodeConfigAppliedEvent.node_name must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class ControlPlaneConfigApplyCompletedEvent:
    runtime: dict[str, object]
    expected_counts: dict[str, int]
    applied_counts: dict[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, dict):
            raise ValueError("ControlPlaneConfigApplyCompletedEvent.runtime must be a mapping")
        _validate_non_negative_int_map(
            self.expected_counts,
            field_name="ControlPlaneConfigApplyCompletedEvent.expected_counts",
        )
        _validate_non_negative_int_map(
            self.applied_counts,
            field_name="ControlPlaneConfigApplyCompletedEvent.applied_counts",
        )


@dataclass(frozen=True, slots=True)
class ControlPlaneGroupSpec:
    group_name: str
    workers: int = 1
    nodes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.group_name, str) or not self.group_name:
            raise ValueError("ControlPlaneGroupSpec.group_name must be a non-empty string")
        if not isinstance(self.workers, int) or self.workers <= 0:
            raise ValueError("ControlPlaneGroupSpec.workers must be an integer > 0")


@dataclass(frozen=True, slots=True)
class ControlPlaneLaunchPlan:
    groups: tuple[ControlPlaneGroupSpec, ...]
    created_at_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True, slots=True)
class ControlPlaneLaunchPlanEvent:
    plan: ControlPlaneLaunchPlan


@dataclass(frozen=True, slots=True)
class ControlPlaneStartWorkEvent:
    source_targets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if any(not isinstance(target, str) or not target for target in self.source_targets):
            raise ValueError(
                "ControlPlaneStartWorkEvent.source_targets must contain non-empty strings"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneLeafStartWorkEvent:
    source_targets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if any(not isinstance(target, str) or not target for target in self.source_targets):
            raise ValueError(
                "ControlPlaneLeafStartWorkEvent.source_targets must contain non-empty strings"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneDagAssembledEvent:
    runtime: dict[str, object]
    plan: ControlPlaneLaunchPlan

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, dict):
            raise ValueError("ControlPlaneDagAssembledEvent.runtime must be a mapping")
        if not isinstance(self.plan, ControlPlaneLaunchPlan):
            raise ValueError("ControlPlaneDagAssembledEvent.plan must be a ControlPlaneLaunchPlan")


@dataclass(frozen=True, slots=True)
class ControlPlaneSpawnRequestedEvent:
    group_name: str
    workers: int
    nodes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.group_name, str) or not self.group_name:
            raise ValueError("ControlPlaneSpawnRequestedEvent.group_name must be a non-empty string")
        if not isinstance(self.workers, int) or self.workers <= 0:
            raise ValueError("ControlPlaneSpawnRequestedEvent.workers must be an integer > 0")


@dataclass(frozen=True, slots=True)
class ControlPlaneRootLeafStopRequestEvent:
    target_group: str
    worker_id: str
    command_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.target_group, "ControlPlaneRootLeafStopRequestEvent.target_group")
        _require_non_empty_str(self.worker_id, "ControlPlaneRootLeafStopRequestEvent.worker_id")
        _require_non_empty_str(self.command_id, "ControlPlaneRootLeafStopRequestEvent.command_id")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason):
            raise ValueError(
                "ControlPlaneRootLeafStopRequestEvent.reason must be a non-empty string when provided"
            )


@dataclass(frozen=True, slots=True)
class ControlPlaneRootLeafBoundaryExecuteRequestEvent:
    target_group: str
    worker_id: str
    request_id: str
    inputs: tuple[object, ...] = field(default_factory=tuple)
    finalize: bool = True

    def __post_init__(self) -> None:
        _require_non_empty_str(
            self.target_group,
            "ControlPlaneRootLeafBoundaryExecuteRequestEvent.target_group",
        )
        _require_non_empty_str(
            self.worker_id,
            "ControlPlaneRootLeafBoundaryExecuteRequestEvent.worker_id",
        )
        _require_non_empty_str(
            self.request_id,
            "ControlPlaneRootLeafBoundaryExecuteRequestEvent.request_id",
        )
        if not isinstance(self.finalize, bool):
            raise ValueError(
                "ControlPlaneRootLeafBoundaryExecuteRequestEvent.finalize must be a boolean"
            )


def _require_non_empty_str(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_non_negative_int_map(value: object, *, field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping[str, int]")
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if not isinstance(item, int) or item < 0:
            raise ValueError(f"{field_name} values must be integers >= 0")


__all__ = [
    "ControlPlaneGroupSpec",
    "ControlPlaneDiscoveryCompletedEvent",
    "ControlPlaneDiscoveryItemEvent",
    "ControlPlaneDiscoveryEntityRecord",
    "ControlPlaneDiscoveryStartRequestedEvent",
    "ControlPlaneDiscoveryBatchRequestedEvent",
    "ControlPlaneDiscoveryBatchReadyEvent",
    "ControlPlaneDiscoverySourceCompletedEvent",
    "ControlPlaneDagAssemblyRequestedEvent",
    "discovery_entity_sort_key",
    "ControlPlaneConfigRecord",
    "SystemRuntimeConfigRecord",
    "ObservabilityConfigRecord",
    "ExecutionGroupConfigRecord",
    "NodeConfigRecord",
    "ControlPlaneConfigStreamCompletedEvent",
    "ControlPlaneSystemConfigAppliedEvent",
    "ControlPlaneObservabilityConfigAppliedEvent",
    "ControlPlaneNodeConfigAppliedEvent",
    "ControlPlaneConfigApplyCompletedEvent",
    "ControlPlaneInitEvent",
    "ControlPlaneLaunchPlan",
    "ControlPlaneLaunchPlanEvent",
    "ControlPlaneStartWorkEvent",
    "ControlPlaneLeafStartWorkEvent",
    "ControlPlaneDagAssembledEvent",
    "ControlPlaneLeafPulse",
    "ControlPlaneLeafHelloEvent",
    "ControlPlaneLeafDiscoveryRequestEvent",
    "ControlPlaneLeafDiscoverySnapshotEvent",
    "ControlPlaneLeafDiscoveryAckEvent",
    "ControlPlaneLeafConfigCardEvent",
    "ControlPlaneLeafConfigAckEvent",
    "ControlPlaneLeafStopCommand",
    "ControlPlaneLeafStopAckEvent",
    "ControlPlaneLeafBoundaryExecuteCommand",
    "ControlPlaneLeafBoundaryResultEvent",
    "ControlPlaneLeafDrainReadyEvent",
    "ControlPlaneShutdownReadyEvent",
    "ControlPlaneRootPulse",
    "ControlPlaneSpawnRequestedEvent",
    "ControlPlaneRootLeafStopRequestEvent",
    "ControlPlaneRootLeafBoundaryExecuteRequestEvent",
]
